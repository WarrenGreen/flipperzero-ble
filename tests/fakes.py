import asyncio
from collections.abc import Callable

from flipperzero_ble._proto import flipper_pb2
from flipperzero_ble.framing import Decoder, encode

Handler = Callable[[list[flipper_pb2.Main]], list[flipper_pb2.Main]]


class FakeTransport:
    """Collects each multi-part command and replies with whatever `handler` returns, split into tiny chunks
    to exercise reassembly."""

    def __init__(self, handler: Handler, chunk_size: int = 7):
        self.handler = handler
        self.chunk_size = chunk_size
        self.commands: list[list[flipper_pb2.Main]] = []
        self._decoder = Decoder()
        self._current: list[flipper_pb2.Main] = []
        self._receiver = lambda _: None
        self.closed = False

    def set_receiver(self, callback) -> None:
        self._receiver = callback

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
            for reply in self.handler(command):
                reply.command_id = command[0].command_id
                self.push(reply)

    async def close(self) -> None:
        self.closed = True


def ok(**content) -> flipper_pb2.Main:
    return flipper_pb2.Main(command_status=flipper_pb2.OK, **content)
