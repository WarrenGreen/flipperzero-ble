import asyncio
import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from ._proto import gui_pb2

if TYPE_CHECKING:
    from .client import Flipper

WIDTH = 128
HEIGHT = 64
BACKLIGHT_RGB = (255, 130, 0)
PIXEL_RGB = (0, 0, 0)


class Key(IntEnum):
    UP = gui_pb2.UP
    DOWN = gui_pb2.DOWN
    RIGHT = gui_pb2.RIGHT
    LEFT = gui_pb2.LEFT
    OK = gui_pb2.OK
    BACK = gui_pb2.BACK


@dataclass(frozen=True)
class Screen:
    """A 128x64 monochrome frame. `pixels[y][x]` is True where the pixel is dark."""

    pixels: tuple[tuple[bool, ...], ...]

    @classmethod
    def from_frame(cls, data: bytes, orientation: int = gui_pb2.HORIZONTAL) -> "Screen":
        # Frames use the display's page layout: each byte is a column of 8 vertical pixels.
        rows = tuple(
            tuple(bool(data[(y // 8) * WIDTH + x] >> (y % 8) & 1) for x in range(WIDTH)) for y in range(HEIGHT)
        )
        if orientation == gui_pb2.HORIZONTAL_FLIP:
            rows = tuple(tuple(reversed(row)) for row in reversed(rows))
        return cls(rows)

    def to_text(self) -> str:
        """Renders two pixel rows per line with half-block characters."""
        blocks = {(False, False): " ", (True, False): "▀", (False, True): "▄", (True, True): "█"}
        return "\n".join(
            "".join(blocks[(self.pixels[y][x], self.pixels[y + 1][x])] for x in range(WIDTH))
            for y in range(0, HEIGHT, 2)
        )

    def to_png(self, scale: int = 4) -> bytes:
        """Encodes the frame as a PNG in the Flipper's orange-and-black colours."""
        raw = bytearray()
        for row in self.pixels:
            line = b"".join(bytes(PIXEL_RGB if dark else BACKLIGHT_RGB) * scale for dark in row)
            raw.extend((b"\x00" + line) * scale)

        def chunk(kind: bytes, body: bytes) -> bytes:
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

        header = struct.pack(">IIBBBBB", WIDTH * scale, HEIGHT * scale, 8, 2, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")


class Gui:
    """The Flipper's screen and physical buttons."""

    def __init__(self, flipper: "Flipper"):
        self._flipper = flipper

    async def press(self, key: Key | str, long: bool = False) -> None:
        """Presses and releases a button, the same event sequence a real press produces."""
        key = Key[key.upper()] if isinstance(key, str) else key
        for event in (gui_pb2.PRESS, gui_pb2.LONG if long else gui_pb2.SHORT, gui_pb2.RELEASE):
            await self._flipper.request(gui_send_input_event_request={"key": key, "type": event})

    async def screenshot(self, timeout: float = 5.0) -> Screen:
        frame: asyncio.Future = asyncio.get_running_loop().create_future()

        def on_message(message):
            if message.WhichOneof("content") == "gui_screen_frame" and not frame.done():
                frame.set_result(message.gui_screen_frame)

        unsubscribe = self._flipper.add_listener(on_message)
        try:
            await self._flipper.request(gui_start_screen_stream_request={})
            try:
                received = await asyncio.wait_for(frame, timeout=timeout)
            finally:
                await self._flipper.request(gui_stop_screen_stream_request={})
        finally:
            unsubscribe()
        return Screen.from_frame(received.data, received.orientation)
