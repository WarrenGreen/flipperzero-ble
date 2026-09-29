import asyncio
from dataclasses import dataclass

from bleak import BleakScanner
from bleak.backends.device import BLEDevice

from .errors import FlipperNotFoundError

ADVERTISED_SERVICE = "00003082-0000-1000-8000-00805f9b34fb"
SERIAL_SERVICE = "8fe5b3d5-2e7f-4a98-2a48-7acc60fe0000"


@dataclass(frozen=True)
class FlipperDevice:
    name: str
    address: str
    rssi: int
    ble_device: BLEDevice

    def matches(self, identifier: str) -> bool:
        """Matches the BLE address (a UUID on macOS), the full name, or the bare name ("R3loma")."""
        wanted = identifier.strip().lower()
        name = self.name.lower()
        return wanted in (self.address.lower(), name, name.removeprefix("flipper "))


def _is_flipper(name: str, service_uuids: list[str]) -> bool:
    uuids = {uuid.lower() for uuid in service_uuids}
    return name.lower().startswith("flipper ") or bool(uuids & {ADVERTISED_SERVICE, SERIAL_SERVICE})


async def scan(timeout: float = 10.0) -> list[FlipperDevice]:
    """Returns advertising Flippers, strongest signal first."""
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    flippers = []
    for device, adv in found.values():
        name = adv.local_name or device.name or ""
        if _is_flipper(name, adv.service_uuids):
            flippers.append(FlipperDevice(name or device.address, device.address, adv.rssi, device))
    return sorted(flippers, key=lambda f: -f.rssi)


async def find(identifier: str | None = None, timeout: float = 10.0) -> FlipperDevice:
    """Finds a Flipper by address or name, or the nearest one if no identifier is given."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        flippers = await scan(timeout=min(3.0, max(remaining, 0.5)))
        matches = [f for f in flippers if identifier is None or f.matches(identifier)]
        if matches:
            return matches[0]
        if asyncio.get_running_loop().time() >= deadline:
            wanted = f" matching {identifier!r}" if identifier else ""
            raise FlipperNotFoundError(
                f"No Flipper{wanted} found. Enable Bluetooth on the Flipper and disconnect the mobile app."
            )
