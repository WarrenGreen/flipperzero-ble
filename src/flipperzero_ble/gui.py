import asyncio
import contextlib
import struct
import zlib
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Any

from ._proto import gui_pb2

if TYPE_CHECKING:
    from .client import Flipper, Subscription

WIDTH = 128
HEIGHT = 64
BACKLIGHT_RGB = (255, 130, 0)
PIXEL_RGB = (0, 0, 0)
LONG_PRESS_SECONDS = 0.5
REPEAT_SECONDS = 0.15


class Key(IntEnum):
    UP = gui_pb2.UP
    DOWN = gui_pb2.DOWN
    RIGHT = gui_pb2.RIGHT
    LEFT = gui_pb2.LEFT
    OK = gui_pb2.OK
    BACK = gui_pb2.BACK


class InputType(IntEnum):
    PRESS = gui_pb2.PRESS
    RELEASE = gui_pb2.RELEASE
    SHORT = gui_pb2.SHORT
    LONG = gui_pb2.LONG
    REPEAT = gui_pb2.REPEAT


@dataclass(frozen=True)
class InputEvent:
    key: Key
    type: InputType


def _key(key: Key | str) -> Key:
    return Key[key.upper()] if isinstance(key, str) else key


@dataclass(frozen=True)
class Screen:
    """A monochrome frame, 128x64 for the Flipper's display. `pixels[y][x]` is True where the pixel is dark."""

    pixels: tuple[tuple[bool, ...], ...]

    @property
    def width(self) -> int:
        return len(self.pixels[0]) if self.pixels else 0

    @property
    def height(self) -> int:
        return len(self.pixels)

    @classmethod
    def blank(cls, width: int = WIDTH, height: int = HEIGHT) -> "Screen":
        return cls(tuple((False,) * width for _ in range(height)))

    @classmethod
    def from_pixels(cls, rows: Iterable[Iterable[Any]]) -> "Screen":
        """Builds a frame from rows of truthy (dark) and falsy (lit) values."""
        return cls(tuple(tuple(bool(p) for p in row) for row in rows))

    @classmethod
    def from_art(cls, art: str, dark: str = "#", width: int = WIDTH, height: int = HEIGHT) -> "Screen":
        """Builds a frame from text, one line per row, where `dark` characters are dark pixels. Padded to size."""
        lines = art.splitlines()[:height]
        rows = [[ch in dark for ch in line[:width].ljust(width)] for line in lines]
        rows += [[False] * width for _ in range(height - len(rows))]
        return cls.from_pixels(rows)

    @classmethod
    def from_image(cls, image: Any, threshold: int = 128) -> "Screen":
        """Builds a frame from a Pillow image (any mode), which must already be the display's size.

        Pixels darker than `threshold` (0-255) become dark pixels.
        """
        gray = image.convert("L")
        width, height = gray.size
        data = list(gray.getdata())
        return cls.from_pixels([p < threshold for p in data[y * width : (y + 1) * width]] for y in range(height))

    @classmethod
    def from_frame(cls, data: bytes, orientation: int = gui_pb2.HORIZONTAL) -> "Screen":
        """Decodes a screen stream frame, which uses the display's page layout: each byte is a column of 8
        vertical pixels. A frame from a Flipper held upside down (HORIZONTAL_FLIP) is turned the right way up."""
        rows = tuple(
            tuple(bool(data[(y // 8) * WIDTH + x] >> (y % 8) & 1) for x in range(WIDTH)) for y in range(HEIGHT)
        )
        if orientation == gui_pb2.HORIZONTAL_FLIP:
            rows = tuple(tuple(reversed(row)) for row in reversed(rows))
        return cls(rows)

    def to_frame(self) -> bytes:
        """Encodes a 128x64 frame in the display's page layout (the inverse of from_frame)."""
        self._require_display_size()
        data = bytearray(WIDTH * HEIGHT // 8)
        for y, row in enumerate(self.pixels):
            for x, dark in enumerate(row):
                if dark:
                    data[(y // 8) * WIDTH + x] |= 1 << (y % 8)
        return bytes(data)

    @classmethod
    def from_xbm(cls, data: bytes, width: int = WIDTH, height: int = HEIGHT) -> "Screen":
        """Decodes XBM bitmap data: rows of bytes, least significant bit first, set bits dark."""
        stride = (width + 7) // 8
        return cls.from_pixels(
            (data[y * stride + x // 8] >> (x % 8) & 1 for x in range(width)) for y in range(height)
        )

    def to_xbm(self) -> bytes:
        """Encodes the frame as XBM bitmap data, the format the virtual display takes."""
        stride = (self.width + 7) // 8
        data = bytearray(stride * self.height)
        for y, row in enumerate(self.pixels):
            for x, dark in enumerate(row):
                if dark:
                    data[y * stride + x // 8] |= 1 << (x % 8)
        return bytes(data)

    def _require_display_size(self) -> None:
        if (self.width, self.height) != (WIDTH, HEIGHT):
            raise ValueError(f"Frames must be {WIDTH}x{HEIGHT}, not {self.width}x{self.height}")

    def to_text(self) -> str:
        """Renders two pixel rows per line with half-block characters."""
        blocks = {(False, False): " ", (True, False): "▀", (False, True): "▄", (True, True): "█"}
        rows = list(self.pixels) + ([(False,) * self.width] if self.height % 2 else [])
        return "\n".join(
            "".join(blocks[(rows[y][x], rows[y + 1][x])] for x in range(self.width)) for y in range(0, len(rows), 2)
        )

    def to_png(self, scale: int = 4) -> bytes:
        """Encodes the frame as a PNG in the Flipper's orange-and-black colours."""
        raw = bytearray()
        for row in self.pixels:
            line = b"".join(bytes(PIXEL_RGB if dark else BACKLIGHT_RGB) * scale for dark in row)
            raw.extend((b"\x00" + line) * scale)

        def chunk(kind: bytes, body: bytes) -> bytes:
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

        header = struct.pack(">IIBBBBB", self.width * scale, self.height * scale, 8, 2, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")


class VirtualDisplay:
    """Replaces the Flipper's screen with frames you draw, while the `async with` block runs.

    With `forward_input=True`, the Flipper's buttons are sent to you (see `inputs()`) instead of to the Flipper,
    so Back won't leave the virtual display.
    """

    def __init__(self, flipper: "Flipper", first_frame: Screen | None = None, forward_input: bool = False):
        self._flipper = flipper
        self._first_frame = first_frame
        self._forward_input = forward_input
        self._inputs: Subscription | None = None

    async def __aenter__(self) -> "VirtualDisplay":
        if self._forward_input:
            self._inputs = self._flipper.subscribe("gui_send_input_event_request")
        request: dict = {"send_input": self._forward_input}
        if self._first_frame is not None:
            request["first_frame"] = {"data": self._frame_data(self._first_frame)}
        try:
            await self._flipper.request(gui_start_virtual_display_request=request)
        except BaseException:
            self._close_inputs()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            if self._flipper.connected:
                await self._flipper.request(gui_stop_virtual_display_request={})
        finally:
            self._close_inputs()

    def _close_inputs(self) -> None:
        if self._inputs is not None:
            self._inputs.close()

    @staticmethod
    def _frame_data(screen: Screen) -> bytes:
        screen._require_display_size()
        return screen.to_xbm()

    async def draw(self, screen: Screen) -> None:
        """Shows a 128x64 frame. Frames aren't acknowledged, so this returns as soon as it's sent."""
        await self._flipper.send(gui_screen_frame={"data": self._frame_data(screen)})

    async def inputs(self) -> AsyncIterator[InputEvent]:
        """Button events from the Flipper. Requires forward_input=True."""
        if self._inputs is None:
            raise RuntimeError("Open the virtual display with forward_input=True to receive button events")
        async for event in self._inputs:
            yield InputEvent(Key(event.key), InputType(event.type))


class Gui:
    """The Flipper's screen and physical buttons."""

    def __init__(self, flipper: "Flipper"):
        self._flipper = flipper

    async def send_input(self, key: Key | str, event: InputType | str) -> None:
        """Sends one raw input event. Most apps expect the full sequence that press() and hold() send."""
        event = InputType[event.upper()] if isinstance(event, str) else event
        await self._flipper.request(gui_send_input_event_request={"key": _key(key), "type": event})

    async def press(self, key: Key | str, long: bool = False) -> None:
        """Presses and releases a button, the same event sequence a real press produces."""
        key = _key(key)
        for event in (InputType.PRESS, InputType.LONG if long else InputType.SHORT, InputType.RELEASE):
            await self.send_input(key, event)

    async def hold(self, key: Key | str, seconds: float) -> None:
        """Holds a button down for `seconds`, sending the repeat events a held button produces (fast scrolling)."""
        key = _key(key)
        await self.send_input(key, InputType.PRESS)
        if seconds < LONG_PRESS_SECONDS:
            await asyncio.sleep(seconds)
            await self.send_input(key, InputType.SHORT)
        else:
            await asyncio.sleep(LONG_PRESS_SECONDS)
            await self.send_input(key, InputType.LONG)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + seconds - LONG_PRESS_SECONDS
            while loop.time() + REPEAT_SECONDS <= deadline:
                await asyncio.sleep(REPEAT_SECONDS)
                await self.send_input(key, InputType.REPEAT)
        await self.send_input(key, InputType.RELEASE)

    @contextlib.asynccontextmanager
    async def stream(self, maxsize: int = 1) -> AsyncIterator[AsyncIterator[Screen]]:
        """Live screen frames: `async with flipper.gui.stream() as frames: async for screen in frames: ...`

        The Flipper sends a frame whenever the screen changes. By default only the newest unread frame is kept.
        """
        async with self._flipper.stream(
            "gui_screen_frame",
            start={"gui_start_screen_stream_request": {}},
            stop={"gui_stop_screen_stream_request": {}},
            maxsize=maxsize,
        ) as frames:
            yield (Screen.from_frame(frame.data, frame.orientation) async for frame in frames)

    async def screenshot(self, timeout: float = 5.0) -> Screen:
        async with self._flipper.stream(
            "gui_screen_frame",
            start={"gui_start_screen_stream_request": {}},
            stop={"gui_stop_screen_stream_request": {}},
            maxsize=1,
        ) as frames:
            frame = await frames.get(timeout=timeout)
        return Screen.from_frame(frame.data, frame.orientation)

    def virtual_display(self, first_frame: Screen | None = None, forward_input: bool = False) -> VirtualDisplay:
        """`async with flipper.gui.virtual_display() as display: await display.draw(screen)`"""
        return VirtualDisplay(self._flipper, first_frame, forward_input)
