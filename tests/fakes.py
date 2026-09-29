import asyncio
from collections.abc import Callable

from flipperzero_ble._proto import application_pb2, flipper_pb2
from flipperzero_ble.framing import Decoder, encode

Handler = Callable[[list[flipper_pb2.Main]], list[flipper_pb2.Main]]


class FakeTransport:
    """Collects each multi-part command and replies with whatever `handler` returns, split into tiny chunks
    to exercise reassembly. Like the real firmware, it announces APP_STARTED/APP_CLOSED after an app in RPC
    mode is started or successfully exited."""

    def __init__(self, handler: Handler, chunk_size: int = 7):
        self.handler = handler
        self.chunk_size = chunk_size
        self.commands: list[list[flipper_pb2.Main]] = []
        self.sent_without_reply: list[flipper_pb2.Main] = []
        self._decoder = Decoder()
        self._current: list[flipper_pb2.Main] = []
        self._receiver = lambda _: None
        self._on_disconnect = lambda: None
        self.closed = False

    def set_receiver(self, callback) -> None:
        self._receiver = callback

    def set_disconnect_callback(self, callback) -> None:
        self._on_disconnect = callback

    def disconnect(self) -> None:
        asyncio.get_running_loop().call_soon(self._on_disconnect)

    def push(self, message: flipper_pb2.Main) -> None:
        data = encode(message)
        loop = asyncio.get_running_loop()
        for i in range(0, len(data), self.chunk_size):
            loop.call_soon(self._receiver, data[i : i + self.chunk_size])

    async def send(self, data: bytes) -> None:
        for message in self._decoder.feed(data):
            self._current.append(message)
            if message.has_next:
                continue
            command, self._current = self._current, []
            self.commands.append(command)
            replies = self.handler(command)
            if replies is None:
                self.sent_without_reply.append(command[0])
                continue
            for reply in replies:
                reply.command_id = command[0].command_id
                self.push(reply)
            if replies and replies[-1].command_status == flipper_pb2.OK:
                self._announce_app_state(command[0])

    def _announce_app_state(self, command: flipper_pb2.Main) -> None:
        kind = command.WhichOneof("content")
        if kind == "app_start_request" and command.app_start_request.args == "RPC":
            self.push(flipper_pb2.Main(app_state_response={"state": application_pb2.APP_STARTED}))
        elif kind == "app_exit_request":
            self.push(flipper_pb2.Main(app_state_response={"state": application_pb2.APP_CLOSED}))

    async def close(self) -> None:
        self.closed = True


def ok(**content) -> flipper_pb2.Main:
    return flipper_pb2.Main(command_status=flipper_pb2.OK, **content)


def kinds(transport: FakeTransport) -> list[str]:
    return [command[0].WhichOneof("content") for command in transport.commands]


class FakeFilesystem:
    """A handler that serves storage commands from memory. Directory paths are kept in `dirs`."""

    def __init__(self, files: dict[str, bytes] | None = None):
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {"/ext"}
        for path, data in (files or {}).items():
            self._add_parents(path)
            self.files[path] = data

    def _add_parents(self, path: str) -> None:
        parent = path.rsplit("/", 1)[0]
        while parent.count("/") >= 1 and parent not in self.dirs:
            self.dirs.add(parent)
            parent = parent.rsplit("/", 1)[0]

    def _children(self, path: str) -> list[dict]:
        prefix = path.rstrip("/") + "/"
        names = {p[len(prefix) :].split("/")[0] for p in [*self.files, *self.dirs] if p.startswith(prefix)}
        return [
            {"name": n, "type": 1}
            if prefix + n in self.dirs
            else {"name": n, "size": len(self.files[prefix + n])}
            for n in sorted(names)
        ]

    def __call__(self, command: list[flipper_pb2.Main]) -> list[flipper_pb2.Main]:
        first = command[0]
        kind = first.WhichOneof("content")
        request = getattr(first, kind)
        missing = [flipper_pb2.Main(command_status=flipper_pb2.ERROR_STORAGE_NOT_EXIST)]
        if kind == "storage_list_request":
            if request.path not in self.dirs:
                return missing
            files = self._children(request.path)
            pages = [files[i : i + 8] for i in range(0, len(files), 8)] or [[]]
            return [ok(has_next=i < len(pages) - 1, storage_list_response={"file": page}) for i, page in enumerate(pages)]
        if kind == "storage_stat_request":
            if request.path in self.dirs:
                return [ok(storage_stat_response={"file": {"type": 1}})]
            if request.path in self.files:
                return [ok(storage_stat_response={"file": {"size": len(self.files[request.path])}})]
            return missing
        if kind == "storage_read_request":
            if request.path not in self.files:
                return missing
            data = self.files[request.path]
            chunks = [data[i : i + 512] for i in range(0, len(data), 512)] or [b""]
            return [ok(has_next=i < len(chunks) - 1, storage_read_response={"file": {"data": c}}) for i, c in enumerate(chunks)]
        if kind == "storage_write_request":
            if request.path.rsplit("/", 1)[0] not in self.dirs:
                return missing
            self.files[request.path] = b"".join(part.storage_write_request.file.data for part in command)
            return [ok()]
        if kind == "storage_delete_request":
            existed = request.path in self.files or request.path in self.dirs
            self.files.pop(request.path, None)
            self.dirs.discard(request.path)
            if request.recursive:
                for p in [*self.files, *self.dirs]:
                    if p.startswith(request.path + "/"):
                        self.files.pop(p, None); self.dirs.discard(p)
            return [ok()] if existed else missing
        if kind == "storage_mkdir_request":
            if request.path in self.dirs:
                return [flipper_pb2.Main(command_status=flipper_pb2.ERROR_STORAGE_EXIST)]
            if request.path.rsplit("/", 1)[0] not in self.dirs:
                return missing
            self.dirs.add(request.path)
            return [ok()]
        if kind.startswith("storage_"):
            raise AssertionError(f"FakeFilesystem can't handle {kind}")
        return [ok()]  # non-storage commands (app start/exit, gui input) just succeed
