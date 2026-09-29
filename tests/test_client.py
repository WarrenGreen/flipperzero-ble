import asyncio

import pytest

from flipperzero_ble import FileEntry, Flipper, FlipperError
from flipperzero_ble._proto import flipper_pb2, gui_pb2
from flipperzero_ble.storage import WRITE_CHUNK_SIZE

from .fakes import FakeTransport, ok


def run(coro):
    return asyncio.run(coro)


def test_request_collects_multi_part_response():
    def handler(command):
        assert command[0].storage_list_request.path == "/ext"
        first = ok(has_next=True, storage_list_response={"file": [{"name": "a.sub", "size": 10}]})
        second = ok(storage_list_response={"file": [{"name": "dir", "type": 1}]})
        return [first, second]

    async def main():
        flipper = Flipper(FakeTransport(handler))
        return await flipper.storage.list("/ext")

    assert run(main()) == [FileEntry("a.sub", False, 10), FileEntry("dir", True, 0)]


def test_error_status_raises_with_name():
    def handler(command):
        return [flipper_pb2.Main(command_status=flipper_pb2.ERROR_STORAGE_NOT_EXIST)]

    async def main():
        flipper = Flipper(FakeTransport(handler))
        with pytest.raises(FlipperError) as exc:
            await flipper.storage.read("/ext/missing")
        assert exc.value.status == "ERROR_STORAGE_NOT_EXIST"
        assert await flipper.storage.stat("/ext/missing") is None
        await flipper.storage.delete("/ext/missing", missing_ok=True)

    run(main())


def test_write_is_chunked_with_has_next():
    transport = FakeTransport(lambda command: [ok()])
    data = bytes(range(256)) * 5

    async def main():
        await Flipper(transport).storage.write("/ext/big.bin", data)

    run(main())
    (command,) = transport.commands
    assert [part.has_next for part in command] == [True, True, False]
    assert all(len(part.storage_write_request.file.data) <= WRITE_CHUNK_SIZE for part in command)
    assert b"".join(part.storage_write_request.file.data for part in command) == data


def test_device_info_merges_key_values():
    def handler(command):
        return [
            ok(has_next=True, system_device_info_response={"key": "hardware_name", "value": "R3loma"}),
            ok(system_device_info_response={"key": "firmware_version", "value": "1.4.3"}),
        ]

    async def main():
        return await Flipper(FakeTransport(handler)).system.device_info()

    assert run(main()) == {"hardware_name": "R3loma", "firmware_version": "1.4.3"}


def test_screenshot_uses_unsolicited_frame():
    frame = bytearray(1024)
    frame[0] = 0b1

    async def main():
        transport = FakeTransport(lambda command: [ok()])

        def handler(command):
            if command[0].WhichOneof("content") == "gui_start_screen_stream_request":
                transport.push(flipper_pb2.Main(command_id=0, gui_screen_frame={"data": bytes(frame)}))
            return [ok()]

        transport.handler = handler
        return await Flipper(transport).gui.screenshot()

    screen = run(main())
    assert screen.pixels[0][0] is True
    assert sum(sum(row) for row in screen.pixels) == 1


def test_gui_press_sends_press_short_release():
    transport = FakeTransport(lambda command: [ok()])

    async def main():
        await Flipper(transport).gui.press("ok")

    run(main())
    events = [(c[0].gui_send_input_event_request.key, c[0].gui_send_input_event_request.type) for c in transport.commands]
    assert events == [(gui_pb2.OK, gui_pb2.PRESS), (gui_pb2.OK, gui_pb2.SHORT), (gui_pb2.OK, gui_pb2.RELEASE)]


def test_write_stops_sending_once_the_flipper_rejects_it():
    def handler(command):
        return [flipper_pb2.Main(command_status=flipper_pb2.ERROR_STORAGE_DENIED)]

    transport = FakeTransport(handler)

    async def main():
        flipper = Flipper(transport)
        # The fake only replies to complete commands, so reply to the first part as the firmware would.
        original_send = transport.send

        async def send(data):
            await original_send(data)
            if transport._current and len(transport._current) == 1:
                transport.push(flipper_pb2.Main(command_id=transport._current[0].command_id,
                                                command_status=flipper_pb2.ERROR_STORAGE_DENIED))
                await asyncio.sleep(0)

        transport.send = send
        with pytest.raises(FlipperError) as exc:
            await flipper.storage.write("/int/locked.bin", bytes(WRITE_CHUNK_SIZE * 4))
        assert exc.value.status == "ERROR_STORAGE_DENIED"

    run(main())
    assert len(transport._current) < 4


def test_write_and_read_report_progress():
    from .fakes import FakeFilesystem

    fs = FakeFilesystem()
    data = bytes(1300)
    written, read = [], []

    async def main():
        flipper = Flipper(FakeTransport(fs))
        await flipper.storage.write("/ext/a.bin", data, progress=lambda done, total: written.append((done, total)))
        return await flipper.storage.read("/ext/a.bin", progress=lambda done, total: read.append((done, total)))

    assert run(main()) == data
    assert written == [(512, 1300), (1024, 1300), (1300, 1300)]
    assert read == [(512, 1300), (1024, 1300), (1300, 1300)]


def test_disconnect_fails_pending_requests_and_subscriptions():
    from flipperzero_ble.errors import FlipperDisconnectedError

    transport = FakeTransport(lambda command: [])

    async def main():
        flipper = Flipper(transport)
        subscription = flipper.subscribe("desktop_status")
        pending = asyncio.ensure_future(flipper.system.ping(b"x"))
        await asyncio.sleep(0)
        transport.disconnect()
        with pytest.raises(FlipperDisconnectedError):
            await pending
        with pytest.raises(FlipperDisconnectedError):
            await subscription.get(timeout=1)
        assert not flipper.connected
        await asyncio.wait_for(flipper.wait_disconnected(), 1)
        with pytest.raises(FlipperDisconnectedError):
            await flipper.system.ping()

    run(main())


def test_subscription_keeps_newest_messages_when_bounded():
    transport = FakeTransport(lambda command: [ok()])

    async def main():
        flipper = Flipper(transport)
        with flipper.subscribe("desktop_status", maxsize=2) as statuses:
            for locked in (True, False, True):
                transport.push(flipper_pb2.Main(desktop_status={"locked": locked}))
            transport.push(flipper_pb2.Main(gui_screen_frame={"data": b""}))  # other kinds are ignored
            await asyncio.sleep(0.01)
            received = [(await statuses.get(timeout=1)).locked for _ in range(2)]
        assert [s async for s in statuses] == []  # closed subscriptions end iteration
        return received

    assert run(main()) == [False, True]


def test_listeners_still_receive_unprompted_messages():
    transport = FakeTransport(lambda command: [ok()])
    seen = []

    async def main():
        flipper = Flipper(transport)
        unsubscribe = flipper.add_listener(lambda message: seen.append(message.WhichOneof("content")))
        transport.push(flipper_pb2.Main(desktop_status={"locked": True}))
        await asyncio.sleep(0.01)
        unsubscribe()
        transport.push(flipper_pb2.Main(desktop_status={"locked": True}))
        await asyncio.sleep(0.01)

    run(main())
    assert seen == ["desktop_status"]
