import asyncio
import zlib

from flipperzero_ble import Flipper, Screen
from flipperzero_ble.subghz import SCRATCH_PATH, key_file, parse

from .fakes import FakeTransport, ok


def test_key_file_round_trips_through_parse():
    contents = key_file("Princeton", 0x060716, 24, TE=399, Guard_time=30)
    fields = parse(contents)
    assert fields["Protocol"] == "Princeton"
    assert fields["Frequency"] == "433920000"
    assert fields["Key"] == "00 00 00 00 00 06 07 16"
    assert fields["TE"] == "399"


def test_session_loads_before_every_press_and_cleans_up():
    transport = FakeTransport(lambda command: [ok()])

    async def main():
        flipper = Flipper(transport)
        async with flipper.subghz.session() as session:
            await session.send(b"contents", seconds=0)
            await session.send(b"contents", seconds=0)

    asyncio.run(main())
    kinds = [c[0].WhichOneof("content") for c in transport.commands]
    send = ["storage_write_request", "app_load_file_request", "app_button_press_request", "app_button_release_request"]
    assert kinds == ["app_start_request", *send, *send, "app_exit_request", "app_lock_status_request", "storage_delete_request"]
    assert transport.commands[-1][0].storage_delete_request.path == SCRATCH_PATH


def test_screen_page_layout_and_png():
    data = bytearray(1024)
    data[127] = 0b1000_0000  # x=127, y=7
    data[128] = 0b0000_0001  # x=0, y=8
    screen = Screen.from_frame(bytes(data))
    assert screen.pixels[7][127] and screen.pixels[8][0]
    assert sum(sum(row) for row in screen.pixels) == 2

    png = screen.to_png(scale=2)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    idat_start = png.index(b"IDAT") + 4
    idat_length = int.from_bytes(png[idat_start - 8 : idat_start - 4], "big")
    raw = zlib.decompress(png[idat_start : idat_start + idat_length])
    assert len(raw) == (1 + 128 * 2 * 3) * 64 * 2
