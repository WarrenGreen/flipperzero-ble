import asyncio
from collections.abc import Callable
from typing import Protocol

from bleak import BleakClient

from .discovery import FlipperDevice
from .errors import FlipperDisconnectedError

TX_TO_FLIPPER = "19ed82ae-ed21-4c9d-4145-228e62fe0000"
RX_FROM_FLIPPER = "19ed82ae-ed21-4c9d-4145-228e61fe0000"
FLOW_CONTROL = "19ed82ae-ed21-4c9d-4145-228e63fe0000"
ATT_HEADER_SIZE = 3


class Transport(Protocol):
    def set_receiver(self, callback: Callable[[bytes], None]) -> None: ...

    def set_disconnect_callback(self, callback: Callable[[], None]) -> None: ...

    async def send(self, data: bytes) -> None: ...

    async def close(self) -> None: ...


class BleTransport:
    """The Flipper's BLE serial service: writes go to one characteristic, replies arrive as indications on
    another, and a flow-control characteristic reports how many bytes the Flipper can currently accept."""

    def __init__(self, client: BleakClient):
        self._client = client
        self._receiver: Callable[[bytes], None] = lambda _: None
        self._on_disconnect: Callable[[], None] = lambda: None
        self._free_space = 0
        self._space_available = asyncio.Event()
        self._connected = True

    @classmethod
    async def connect(cls, device: FlipperDevice, pairing_timeout: float = 120.0) -> "BleTransport":
        transport: BleTransport | None = None

        def disconnected(_client: BleakClient) -> None:
            if transport is not None:
                transport._handle_disconnect()

        client = BleakClient(device.ble_device, timeout=30.0, disconnected_callback=disconnected)
        await client.connect()
        transport = cls(client)
        try:
            # The first encrypted read triggers OS pairing (macOS shows a PIN dialog), so allow time for it.
            initial = await asyncio.wait_for(client.read_gatt_char(FLOW_CONTROL), timeout=pairing_timeout)
            transport._set_free_space(initial)
            await client.start_notify(FLOW_CONTROL, lambda _, data: transport._set_free_space(data))
            await client.start_notify(RX_FROM_FLIPPER, lambda _, data: transport._receiver(bytes(data)))
        except BaseException:
            await client.disconnect()
            raise
        return transport

    def set_receiver(self, callback: Callable[[bytes], None]) -> None:
        self._receiver = callback

    def set_disconnect_callback(self, callback: Callable[[], None]) -> None:
        self._on_disconnect = callback

    def _handle_disconnect(self) -> None:
        if self._connected:
            self._connected = False
            self._space_available.set()  # wake a blocked send() so it can fail
            self._on_disconnect()

    def _set_free_space(self, data: bytearray):
        self._free_space = int.from_bytes(data[:4], "big")
        if self._free_space > 0:
            self._space_available.set()
        else:
            self._space_available.clear()

    async def send(self, data: bytes) -> None:
        chunk_limit = self._client.mtu_size - ATT_HEADER_SIZE
        while data:
            await self._space_available.wait()
            if not self._connected:
                raise FlipperDisconnectedError("The Flipper disconnected")
            size = min(len(data), chunk_limit, self._free_space)
            chunk, data = data[:size], data[size:]
            self._free_space -= size
            if self._free_space <= 0:
                self._space_available.clear()
            await self._client.write_gatt_char(TX_TO_FLIPPER, chunk, response=True)

    async def close(self) -> None:
        self._connected = False
        await self._client.disconnect()
