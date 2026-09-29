import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Iterable

from ._proto import flipper_pb2
from .apps import Apps
from .desktop import Desktop
from .discovery import FlipperDevice, find
from .emulation import IButton, Nfc, Rfid
from .errors import FlipperDisconnectedError, FlipperError
from .framing import Decoder, encode
from .gpio import Gpio
from .gui import Gui
from .infrared import Infrared
from .scripts import Scripts
from .storage import Storage
from .subghz import SubGhz
from .system import System
from .transport import BleTransport, Transport

Listener = Callable[[flipper_pb2.Main], None]
_DISCONNECTED = object()


class Subscription:
    """Messages of one kind that the Flipper sends unprompted (screen frames, app state changes, ...).

    Iterate with `async for`, or call `get()`. `kind` is a PB.Main content field name and each item is that
    field's message, e.g. a `ScreenFrame` for "gui_screen_frame". With `maxsize`, the oldest waiting message
    is dropped when a new one arrives, so a slow reader sees recent messages instead of falling behind.
    """

    def __init__(self, flipper: "Flipper", kind: str, maxsize: int = 0):
        self._flipper = flipper
        self.kind = kind
        self._maxsize = maxsize
        self._queue: asyncio.Queue = asyncio.Queue()
        self._end: BaseException | None = None
        flipper._subscriptions.add(self)

    def _deliver(self, message: flipper_pb2.Main) -> None:
        if self._end is not None or message.WhichOneof("content") != self.kind:
            return
        if self._maxsize and self._queue.qsize() >= self._maxsize:
            self._queue.get_nowait()
        self._queue.put_nowait(getattr(message, self.kind))

    def _finish(self, end: BaseException) -> None:
        if self._end is None:
            self._end = end
            self._flipper._subscriptions.discard(self)
            self._queue.put_nowait(_DISCONNECTED)

    def close(self) -> None:
        self._finish(EOFError(f"Subscription to {self.kind} is closed"))

    async def get(self, timeout: float | None = None):
        """Waits for the next message. Raises TimeoutError, FlipperDisconnectedError if the link drops, or
        EOFError once the subscription is closed."""
        item = await asyncio.wait_for(self._queue.get(), timeout)
        if item is _DISCONNECTED:
            self._queue.put_nowait(_DISCONNECTED)
            raise self._end
        return item

    def __aiter__(self) -> "Subscription":
        return self

    async def __anext__(self):
        try:
            return await self.get()
        except EOFError:
            raise StopAsyncIteration from None

    def __enter__(self) -> "Subscription":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class Flipper:
    """An RPC session with a Flipper Zero.

    Feature helpers live on `storage`, `system`, `apps`, `gui`, `gpio`, `desktop`, `subghz`, `infrared`, `nfc`,
    `rfid`, `ibutton` and `scripts`. Anything else in the protocol can be sent with `request()` using the PB.Main content
    field name, e.g. `await flipper.request(system_ping_request={"data": b"hi"})`.
    """

    def __init__(self, transport: Transport):
        self._transport = transport
        self._decoder = Decoder()
        self._pending: dict[int, asyncio.Queue] = {}
        self._listeners: list[Listener] = []
        self._subscriptions: set[Subscription] = set()
        self._next_id = 1
        self._connected = True
        self._disconnected = asyncio.Event()
        transport.set_receiver(self._on_data)
        transport.set_disconnect_callback(self._on_disconnect)

        self.storage = Storage(self)
        self.system = System(self)
        self.apps = Apps(self)
        self.gui = Gui(self)
        self.gpio = Gpio(self)
        self.desktop = Desktop(self)
        self.subghz = SubGhz(self)
        self.infrared = Infrared(self)
        self.nfc = Nfc(self)
        self.rfid = Rfid(self)
        self.ibutton = IButton(self)
        self.scripts = Scripts(self)

    @classmethod
    async def open(
        cls,
        device: FlipperDevice | str | None = None,
        *,
        scan_timeout: float = 10.0,
        pairing_timeout: float = 120.0,
    ) -> "Flipper":
        """Connects over BLE. `device` may be a FlipperDevice, an address, a name, or None for the nearest."""
        if not isinstance(device, FlipperDevice):
            device = await find(device, timeout=scan_timeout)
        return cls(await BleTransport.connect(device, pairing_timeout=pairing_timeout))

    @property
    def connected(self) -> bool:
        return self._connected

    async def wait_disconnected(self) -> None:
        """Returns once the connection is closed or lost, e.g. the Flipper rebooted or went out of range."""
        await self._disconnected.wait()

    async def close(self) -> None:
        self._mark_disconnected(EOFError("The connection was closed"))
        await self._transport.close()

    async def __aenter__(self) -> "Flipper":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    def add_listener(self, listener: Listener) -> Callable[[], None]:
        """Calls `listener` with every message the Flipper sends unprompted. Returns an unsubscribe function."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def subscribe(self, kind: str, maxsize: int = 0) -> Subscription:
        """Collects unprompted messages whose content is `kind`, e.g. "desktop_status". Close it when done."""
        return Subscription(self, kind, maxsize)

    @contextlib.asynccontextmanager
    async def stream(
        self, kind: str, start: dict | None = None, stop: dict | None = None, maxsize: int = 0
    ) -> AsyncIterator[Subscription]:
        """Subscribes to `kind`, sends the `start` command, and sends `stop` on exit, e.g.
        `stream("desktop_status", {"desktop_status_subscribe_request": {}}, {"desktop_status_unsubscribe_request": {}})`.
        """
        with self.subscribe(kind, maxsize) as subscription:
            if start:
                await self.request(**start)
            try:
                yield subscription
            finally:
                if stop and self._connected:
                    await self.request(**stop)

    def _on_data(self, data: bytes) -> None:
        for message in self._decoder.feed(data):
            pending = self._pending.get(message.command_id)
            if pending is not None:
                pending.put_nowait(message)
                continue
            for subscription in list(self._subscriptions):
                subscription._deliver(message)
            for listener in list(self._listeners):
                listener(message)

    def _on_disconnect(self) -> None:
        self._mark_disconnected(FlipperDisconnectedError("The Flipper disconnected"))

    def _mark_disconnected(self, end: BaseException) -> None:
        if not self._connected:
            return
        self._connected = False
        self._disconnected.set()
        for pending in self._pending.values():
            pending.put_nowait(_DISCONNECTED)
        for subscription in list(self._subscriptions):
            subscription._finish(end)

    def _check_connected(self) -> None:
        if not self._connected:
            raise FlipperDisconnectedError("Not connected to the Flipper")

    def _new_id(self) -> int:
        command_id = self._next_id
        self._next_id = self._next_id % 0xFFFF_FFFF + 1
        return command_id

    async def send(self, **content) -> None:
        """Sends a message that gets no reply, such as a virtual display frame."""
        self._check_connected()
        await self._transport.send(encode(flipper_pb2.Main(command_id=self._new_id(), **content)))

    async def iter_request(
        self, parts: Iterable[dict], timeout: float = 30.0, on_sent: Callable[[int], None] | None = None
    ) -> AsyncIterator[flipper_pb2.Main]:
        """Sends a command (split over one or more messages) and yields response parts as they arrive.

        `timeout` is how long to wait for each part, and `on_sent` is called with each part's index once it's
        sent. Raises FlipperError if the Flipper reports an error.
        """
        self._check_connected()
        parts = list(parts)
        command_id = self._new_id()
        replies: asyncio.Queue = asyncio.Queue()
        self._pending[command_id] = replies
        try:
            for i, content in enumerate(parts):
                if not replies.empty():
                    break  # the Flipper replied before the last part arrived, so it rejected the command
                message = flipper_pb2.Main(command_id=command_id, has_next=i < len(parts) - 1, **content)
                await self._transport.send(encode(message))
                if on_sent:
                    on_sent(i)
            while True:
                reply = await asyncio.wait_for(replies.get(), timeout=timeout)
                if reply is _DISCONNECTED:
                    raise FlipperDisconnectedError("The Flipper disconnected before replying")
                if reply.command_status != flipper_pb2.OK:
                    raise FlipperError(flipper_pb2.CommandStatus.Name(reply.command_status), response=reply)
                yield reply
                if not reply.has_next:
                    return
        finally:
            self._pending.pop(command_id, None)

    async def request(self, timeout: float = 30.0, **content) -> list[flipper_pb2.Main]:
        """Sends one command and returns every response part (multi-part replies set has_next).

        `timeout` is how long to wait for each part.
        """
        return await self.request_stream([content], timeout=timeout)

    async def request_stream(
        self, parts: Iterable[dict], timeout: float = 30.0, on_sent: Callable[[int], None] | None = None
    ) -> list[flipper_pb2.Main]:
        """Sends a command split over several messages (e.g. a chunked file write) and returns the response."""
        async with contextlib.aclosing(self.iter_request(parts, timeout, on_sent)) as replies:
            return [reply async for reply in replies]


class _Connection:
    def __init__(self, device, kwargs):
        self._device = device
        self._kwargs = kwargs
        self._flipper: Flipper | None = None

    def __await__(self):
        return Flipper.open(self._device, **self._kwargs).__await__()

    async def __aenter__(self) -> Flipper:
        self._flipper = await Flipper.open(self._device, **self._kwargs)
        return self._flipper

    async def __aexit__(self, *exc) -> None:
        await self._flipper.close()


def connect(device: FlipperDevice | str | None = None, **kwargs) -> _Connection:
    """`async with connect() as flipper:` or `flipper = await connect()`. See Flipper.open for arguments."""
    return _Connection(device, kwargs)
