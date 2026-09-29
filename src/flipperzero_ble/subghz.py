import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import Flipper

APP_NAME = "Sub-GHz"
SCRATCH_PATH = "/ext/subghz/.flipperzero_ble.sub"
DEFAULT_PRESET = "FuriHalSubGhzPresetOok650Async"


def key_file(
    protocol: str,
    key: int,
    bits: int,
    frequency: int = 433_920_000,
    preset: str = DEFAULT_PRESET,
    **extra: int | str,
) -> bytes:
    """Builds a `.sub` key file for a static-code protocol.

    Extra fields are written as-is, e.g. `key_file("Princeton", 0x060716, 24, TE=399, Guard_time=30)`.
    """
    key_bytes = " ".join(f"{b:02X}" for b in key.to_bytes(8, "big"))
    lines = [
        "Filetype: Flipper SubGhz Key File",
        "Version: 1",
        f"Frequency: {frequency}",
        f"Preset: {preset}",
        f"Protocol: {protocol}",
        f"Bit: {bits}",
        f"Key: {key_bytes}",
        *(f"{name}: {value}" for name, value in extra.items()),
    ]
    return ("\n".join(lines) + "\n").encode()


def parse(contents: bytes | str) -> dict[str, str]:
    """Parses the `Name: value` header of a `.sub` file. Repeated fields (RAW_Data) keep their first value."""
    text = contents.decode("utf-8", errors="replace") if isinstance(contents, bytes) else contents
    fields: dict[str, str] = {}
    for line in text.splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip() and name.strip() not in fields:
            fields[name.strip()] = value.strip()
    return fields


class SubGhzSession:
    """Keeps the Sub-GHz app open in RPC mode for a series of transmissions."""

    def __init__(self, flipper: "Flipper"):
        self._flipper = flipper
        self._used_scratch = False

    async def __aenter__(self) -> "SubGhzSession":
        await self._flipper.apps.start(APP_NAME, "RPC")
        await asyncio.sleep(1.0)
        return self

    async def __aexit__(self, *exc) -> None:
        await self._flipper.apps.exit()
        if self._used_scratch:
            await self._flipper.storage.delete(SCRATCH_PATH, missing_ok=True)

    async def send_file(self, path: str, seconds: float = 0.5) -> None:
        """Transmits a `.sub` file already on the Flipper, holding "send" for `seconds`."""
        # The app rejects a second press on the same load, so load before every press.
        await self._flipper.apps.load_file(path)
        await self._flipper.apps.press()
        await asyncio.sleep(seconds)
        await self._flipper.apps.release()

    async def send(self, contents: bytes, seconds: float = 0.5) -> None:
        """Transmits `.sub` file contents (e.g. from key_file) via a scratch file on the SD card."""
        await self._flipper.storage.write(SCRATCH_PATH, contents)
        self._used_scratch = True
        await self.send_file(SCRATCH_PATH, seconds)


class SubGhz:
    """Transmit Sub-GHz signals. Only transmit where you're permitted to and on devices you own."""

    def __init__(self, flipper: "Flipper"):
        self._flipper = flipper

    def session(self) -> SubGhzSession:
        return SubGhzSession(self._flipper)

    async def transmit_file(self, path: str, seconds: float = 0.5) -> None:
        async with self.session() as session:
            await session.send_file(path, seconds)

    async def transmit(self, contents: bytes, seconds: float = 0.5) -> None:
        async with self.session() as session:
            await session.send(contents, seconds)
