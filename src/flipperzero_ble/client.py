import asyncio
from collections.abc import Callable, Iterable

from ._proto import flipper_pb2
from .apps import Apps
from .discovery import FlipperDevice, find
from .errors import FlipperError
from .framing import Decoder, encode
from .gui import Gui
from .storage import Storage
from .subghz import SubGhz
from .system import System
from .transport import BleTransport, Transport

Listener = Callable[[flipper_pb2.Main], None]


class Flipper:
    """An RPC session with a Flipper Zero.

    Feature helpers live on `storage`, `system`, `apps`, `gui` and `subghz`. Anything else in the protocol
    can be sent with `request()` using the PB.Main content field name, e.g.
    `await flipper.request(system_ping_request={"data": b"hi"})`.
    """

    def __init__(self, transport: Transport):
        self._transport = transport
        self._decoder = Decoder()
        self._pending: dict[int, tuple[asyncio.Future, list]] = {}
        self._listeners: list[Listener] = []
        self._next_id = 1
        transport.set_receiver(self._on_data)

        self.storage = Storage(self)
        self.system = System(self)
        self.apps = Apps(self)
        self.gui = Gui(self)
        self.subghz = SubGhz(self)

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

    async def close(self) -> None:
        await self._transport.close()

    async def __aenter__(self) -> "Flipper":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    def add_listener(self, listener: Listener) -> Callable[[], None]:
        """Receives messages the Flipper sends unprompted, such as screen frames. Returns an unsubscribe function."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _on_data(self, data: bytes) -> None:
        for message in self._decoder.feed(data):
            pending = self._pending.get(message.command_id)
            if pending is None:
                for listener in list(self._listeners):
                    listener(message)
                continue
            future, parts = pending
            parts.append(message)
            if not message.has_next and not future.done():
                future.set_result(parts)

    async def request(self, timeout: float = 30.0, **content) -> list[flipper_pb2.Main]:
        """Sends one command and returns every response part (multi-part replies set has_next)."""
        return await self.request_stream([content], timeout=timeout)

    async def request_stream(self, parts: Iterable[dict], timeout: float = 30.0) -> list[flipper_pb2.Main]:
        """Sends a command split over several messages (e.g. a chunked file write) and returns the response."""
        parts = list(parts)
        command_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[command_id] = (future, [])
        try:
            for i, content in enumerate(parts):
                message = flipper_pb2.Main(command_id=command_id, has_next=i < len(parts) - 1, **content)
                await self._transport.send(encode(message))
            responses = await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(command_id, None)
        status = responses[-1].command_status
        if status != flipper_pb2.OK:
            raise FlipperError(flipper_pb2.CommandStatus.Name(status))
        return responses


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
