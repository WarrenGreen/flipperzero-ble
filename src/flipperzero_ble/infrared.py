import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .apps import AppSession

if TYPE_CHECKING:
    from .client import Flipper

APP_NAME = "Infrared"
SCRATCH_PATH = "/ext/infrared/.flipperzero_ble.ir"


@dataclass(frozen=True)
class Signal:
    """One button in an `.ir` file. `type` is "parsed" (protocol, address, command) or "raw" (timings)."""

    name: str
    type: str
    fields: dict[str, str] = field(default_factory=dict, compare=False)

    @property
    def protocol(self) -> str | None:
        return self.fields.get("protocol")


def parse(contents: bytes | str) -> list[Signal]:
    """Parses an `.ir` remote file into its signals, in file order."""
    text = contents.decode("utf-8", errors="replace") if isinstance(contents, bytes) else contents
    signals: list[Signal] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not sep or line.startswith("#"):
            continue
        if key == "name":
            current = {}
            signals.append(Signal(value, "", current))
        elif current is not None:
            current[key] = value
    return [Signal(s.name, s.fields.get("type", ""), s.fields) for s in signals]


def _hex32(value: int) -> str:
    return " ".join(f"{b:02X}" for b in value.to_bytes(4, "little"))


def parsed_signal(name: str, protocol: str, address: int, command: int) -> Signal:
    """A decoded signal, e.g. `parsed_signal("Power", "NEC", 0x07, 0x02)`.

    Protocols: NEC, NECext, NEC42, NEC42ext, Samsung32, RC5, RC5X, RC6, SIRC, SIRC15, SIRC20, Kaseikyo, RCA,
    Pioneer.
    """
    return Signal(name, "parsed", {"protocol": protocol, "address": _hex32(address), "command": _hex32(command)})


def raw_signal(name: str, timings: list[int], frequency: int = 38_000, duty_cycle: float = 0.33) -> Signal:
    """A raw signal: alternating mark/space durations in microseconds, starting with a mark."""
    return Signal(
        name,
        "raw",
        {"frequency": str(frequency), "duty_cycle": f"{duty_cycle:.6f}", "data": " ".join(map(str, timings))},
    )


def remote_file(signals: list[Signal]) -> bytes:
    """Builds an `.ir` remote file from signals."""
    lines = ["Filetype: IR signals file", "Version: 1"]
    for signal in signals:
        lines += ["# ", f"name: {signal.name}", f"type: {signal.type}"]
        lines += [f"{key}: {value}" for key, value in signal.fields.items() if key != "type"]
    return ("\n".join(lines) + "\n").encode()


class InfraredSession(AppSession):
    """Keeps the Infrared app open in RPC mode. Load a remote, then send its buttons."""

    def __init__(self, flipper: "Flipper"):
        super().__init__(flipper, APP_NAME)
        self._used_scratch = False

    async def __aexit__(self, *exc) -> None:
        await super().__aexit__(*exc)
        if self._used_scratch and self._flipper.connected:
            await self._flipper.storage.delete(SCRATCH_PATH, missing_ok=True)

    async def load(self, path: str) -> None:
        """Loads an `.ir` remote file stored on the Flipper."""
        await self.load_file(path)

    async def load_signals(self, signals: list[Signal]) -> None:
        """Loads signals built with parsed_signal()/raw_signal() via a scratch file on the SD card."""
        await self._flipper.storage.write(SCRATCH_PATH, remote_file(signals))
        self._used_scratch = True
        await self.load(SCRATCH_PATH)

    async def send(self, button: str | int, seconds: float | None = None) -> None:
        """Sends a button of the loaded remote, by name or index. With `seconds`, holds it (repeating) that long."""
        args, index = (button, 0) if isinstance(button, str) else ("", button)
        if seconds is None:
            await self.press_release(args, index)
            return
        await self.press(args, index)
        try:
            await asyncio.sleep(seconds)
        finally:
            await self.release()


class Infrared:
    """Send infrared signals from `.ir` remote files, or from signals built in Python."""

    def __init__(self, flipper: "Flipper"):
        self._flipper = flipper

    def session(self) -> InfraredSession:
        return InfraredSession(self._flipper)

    async def send(self, path: str, button: str | int, seconds: float | None = None) -> None:
        """Sends one button of the remote file at `path`, e.g. `send("/ext/infrared/TV.ir", "Power")`."""
        async with self.session() as session:
            await session.load(path)
            await session.send(button, seconds)

    async def send_signal(self, signal: Signal, seconds: float | None = None) -> None:
        """Sends a signal built with parsed_signal() or raw_signal()."""
        async with self.session() as session:
            await session.load_signals([signal])
            await session.send(signal.name, seconds)

    async def signals(self, path: str) -> list[Signal]:
        """The signals (buttons) in a remote file on the Flipper."""
        return parse(await self._flipper.storage.read(path))
