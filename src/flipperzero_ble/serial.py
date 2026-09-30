"""Talking to a device on the Flipper's GPIO serial pins (USART: header pin 13 TX, pin 14 RX).

The RPC protocol can't reach the serial pins, so this runs a short script on the Flipper's JS Runner that
drives the UART and streams whatever the device replies to a file, which is read back over Bluetooth.

Two things make this work where a naive relay fails, both learned on hardware:
  * The JS Runner has a ~2 KB stack, so materializing an incoming burst (Uint8Array/hex) overflows it and
    kills the script. Bytes are streamed straight to a file instead, never held in JS.
  * The JS engine runs unrolled statements reliably but mishandles `for`/`while` loops, so the read step is
    emitted as a fixed number of unrolled reads.

This is generic UART plumbing (a WiFi dev board, an ESP32, a UART sensor). For a text CLI such as ESP32
Marauder, `command()` sends a line and returns the reply as text.
"""

import asyncio
from typing import TYPE_CHECKING

from .scripts import ScriptSession

if TYPE_CHECKING:
    from .client import Flipper

DEFAULT_BAUD = 115200
SCRATCH_JS = "/ext/.flipperzero_ble_serial.js"
OUTPUT_PATH = "/ext/.flipperzero_ble_serial.out"
PER_READ_MS = 400


def _js_string(data: bytes) -> str:
    """Encodes bytes as a JS double-quoted string literal (\\xNN escapes), safe for the mJS engine."""
    return '"' + "".join(f"\\x{b:02x}" for b in data) + '"'


def build_script(payload: bytes, baud: int, reads: int, wake: bool) -> str:
    """Builds the relay script: set up the UART, optionally wake/flush, write `payload`, stream reads to a file.

    Kept import-free and deterministic so it can be unit-tested.
    """
    lines = [
        'let storage = require("storage");',
        f'let raw = storage.openFile("{OUTPUT_PATH}", "w", "create_always");',
        'let serial = require("serial");',
        f'serial.setup("usart", {baud});',
    ]
    if wake:
        lines += ['serial.write("\\r\\n");', "serial.readAny(500);", "serial.readAny(200);"]
    if payload:
        lines.append(f"serial.write({_js_string(payload)});")
    # Unrolled reads (loops are unreliable on the JS engine); each appends whatever arrived.
    read = f"c = serial.readAny({PER_READ_MS}); if (c !== undefined) raw.write(c);"
    lines.append("let c;")
    lines += [read] * reads
    lines += ["raw.close();", "serial.end();", 'print("done");']
    return "\n".join(lines) + "\n"


class Serial:
    """A device on the Flipper's GPIO serial pins. Only transmit on lines you're allowed to drive."""

    def __init__(self, flipper: "Flipper"):
        self._flipper = flipper

    async def exchange(
        self,
        payload: bytes = b"",
        *,
        baud: int = DEFAULT_BAUD,
        read_seconds: float = 3.0,
        wake: bool = True,
    ) -> bytes:
        """Sends `payload` over the UART and returns the bytes the device replies within ~`read_seconds`.

        `wake` sends a newline and discards boot noise before the payload (helps a CLI settle). Returns b"" if
        the device says nothing. Raises FlipperDisconnectedError if the link drops.
        """
        reads = max(1, round(read_seconds * 1000 / PER_READ_MS))
        script = build_script(payload, baud, reads, wake)
        await self._flipper.storage.write(SCRATCH_JS, script.encode())
        await self._flipper.storage.delete(OUTPUT_PATH, missing_ok=True)
        # Give the script time to finish its unrolled reads before the runner is closed.
        run_seconds = reads * PER_READ_MS / 1000 + (1.0 if wake else 0.0) + 3.0
        try:
            async with ScriptSession(self._flipper, SCRATCH_JS, scratch=True):
                await asyncio.sleep(run_seconds)
            if await self._flipper.storage.exists(OUTPUT_PATH):
                return await self._flipper.storage.read(OUTPUT_PATH)
            return b""
        finally:
            if self._flipper.connected:
                await self._flipper.storage.delete(OUTPUT_PATH, missing_ok=True)

    async def command(
        self,
        text: str,
        *,
        baud: int = DEFAULT_BAUD,
        read_seconds: float = 3.0,
        newline: str = "\r\n",
        encoding: str = "utf-8",
    ) -> str:
        """Sends a text command (a line ending is appended) and returns the reply decoded as text.

        For a serial CLI such as ESP32 Marauder: `await flipper.serial.command("help")`.
        """
        reply = await self.exchange((text + newline).encode(encoding), baud=baud, read_seconds=read_seconds)
        return reply.decode(encoding, errors="replace")

    async def read(self, *, baud: int = DEFAULT_BAUD, read_seconds: float = 3.0) -> bytes:
        """Listens on the UART without sending anything, e.g. to catch a device's boot output."""
        return await self.exchange(b"", baud=baud, read_seconds=read_seconds, wake=False)
