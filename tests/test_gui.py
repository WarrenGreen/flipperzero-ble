import asyncio

import pytest

from flipperzero_ble import Flipper, InputEvent, InputType, Key, Screen
from flipperzero_ble import gui
from flipperzero_ble._proto import flipper_pb2, gui_pb2

from .fakes import FakeTransport, kinds, ok


def run(coro):
    return asyncio.run(coro)


def frame_with_pixel(x: int, y: int) -> bytes:
    data = bytearray(1024)
    data[(y // 8) * 128 + x] = 1 << (y % 8)
    return bytes(data)


def test_frame_and_xbm_round_trip():
    screen = Screen.from_frame(frame_with_pixel(5, 9))
    assert screen.to_frame() == frame_with_pixel(5, 9)
    xbm = screen.to_xbm()
    assert len(xbm) == 1024 and xbm[9 * 16] == 1 << 5
    assert Screen.from_xbm(xbm) == screen


def test_from_art_pads_to_display_size():
    screen = Screen.from_art("#.\n.#")
    assert (screen.width, screen.height) == (128, 64)
    assert screen.pixels[0][0] and screen.pixels[1][1] and not screen.pixels[0][1]
    assert sum(map(sum, screen.pixels)) == 2


def test_from_image_uses_threshold():
    class FakeImage:
        size = (2, 1)

        def convert(self, mode):
            assert mode == "L"
            return self

        def getdata(self):
            return [10, 200]

    assert Screen.from_image(FakeImage()).pixels == ((True, False),)


def test_odd_height_text_render():
    assert Screen.from_pixels([[1], [0], [1]]).to_text() == "▀\n▀"


def test_virtual_display_draws_and_forwards_input():
    def handler(command):
        if command[0].WhichOneof("content") == "gui_screen_frame":
            return None  # frames aren't acknowledged
        return [ok()]

    transport = FakeTransport(handler)

    async def main():
        flipper = Flipper(transport)
        async with flipper.gui.virtual_display(Screen.blank(), forward_input=True) as display:
            await display.draw(Screen.from_art("#"))
            transport.push(flipper_pb2.Main(gui_send_input_event_request={"key": gui_pb2.OK, "type": gui_pb2.SHORT}))
            async for event in display.inputs():
                return event

    assert run(main()) == InputEvent(Key.OK, InputType.SHORT)
    assert kinds(transport) == ["gui_start_virtual_display_request", "gui_screen_frame", "gui_stop_virtual_display_request"]
    start = transport.commands[0][0].gui_start_virtual_display_request
    assert start.send_input and start.first_frame.data == bytes(1024)
    assert transport.sent_without_reply[0].gui_screen_frame.data[0] == 1


def test_virtual_display_rejects_wrong_size():
    async def main():
        async with Flipper(FakeTransport(lambda c: [ok()])).gui.virtual_display() as display:
            with pytest.raises(ValueError):
                await display.draw(Screen.blank(10, 10))

    run(main())


def test_stream_yields_frames_then_stops():
    async def main():
        transport = FakeTransport(lambda command: [ok()])

        def handler(command):
            if command[0].WhichOneof("content") == "gui_start_screen_stream_request":
                transport.push(flipper_pb2.Main(gui_screen_frame={"data": frame_with_pixel(1, 1)}))
            return [ok()]

        transport.handler = handler
        async with Flipper(transport).gui.stream() as frames:
            async for screen in frames:
                break
        return screen, kinds(transport)

    screen, sent = run(main())
    assert screen.pixels[1][1]
    assert sent == ["gui_start_screen_stream_request", "gui_stop_screen_stream_request"]


def test_hold_sends_long_and_repeats(monkeypatch):
    monkeypatch.setattr(gui, "LONG_PRESS_SECONDS", 0.01)
    monkeypatch.setattr(gui, "REPEAT_SECONDS", 0.01)
    transport = FakeTransport(lambda command: [ok()])

    async def main():
        await Flipper(transport).gui.hold("down", 0.045)

    run(main())
    events = [InputType(c[0].gui_send_input_event_request.type) for c in transport.commands]
    assert events[:2] == [InputType.PRESS, InputType.LONG]
    assert events[-1] == InputType.RELEASE
    assert InputType.REPEAT in events
    assert all(c[0].gui_send_input_event_request.key == gui_pb2.DOWN for c in transport.commands)
