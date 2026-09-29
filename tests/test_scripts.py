import asyncio

from flipperzero_ble import Flipper
from flipperzero_ble._proto import flipper_pb2

from .fakes import FakeFilesystem, FakeTransport, kinds


def run(coro):
    return asyncio.run(coro)


def test_run_uploads_inline_source_starts_js_and_closes_it():
    fs = FakeFilesystem()
    transport = FakeTransport(fs)

    async def main():
        await Flipper(transport).scripts.run("print('hi')\n", seconds=0)

    run(main())
    assert kinds(transport)[:2] == ["storage_write_request", "app_start_request"]
    start = transport.commands[1][0].app_start_request
    assert start.name == "js_app" and start.args == "/ext/.flipperzero_ble_script.js"
    # Back is pressed twice to leave the runner; the scratch script is then deleted.
    assert "gui_send_input_event_request" in kinds(transport)
    assert kinds(transport)[-1] == "storage_delete_request"
    assert "/ext/.flipperzero_ble_script.js" not in fs.files


def test_run_existing_file_is_not_deleted():
    fs = FakeFilesystem({"/ext/apps/Scripts/keep.js": b"print(1)\n"})
    transport = FakeTransport(fs)

    async def main():
        await Flipper(transport).scripts.session("/ext/apps/Scripts/keep.js").__aenter__()

    async def full():
        flipper = Flipper(transport)
        async with flipper.scripts.session("/ext/apps/Scripts/keep.js"):
            pass

    run(full())
    assert transport.commands[0][0].app_start_request.args == "/ext/apps/Scripts/keep.js"
    assert "storage_delete_request" not in kinds(transport)
    assert "/ext/apps/Scripts/keep.js" in fs.files


def test_run_and_read_returns_the_output_file():
    fs = FakeFilesystem()

    async def main():
        flipper = Flipper(FakeTransport(fs))

        async def fake_sleep(_):
            fs.files["/ext/out.txt"] = b"board said hi"

        import flipperzero_ble.scripts as scripts_mod
        real = scripts_mod.asyncio.sleep
        scripts_mod.asyncio.sleep = fake_sleep
        try:
            return await flipper.scripts.run_and_read("code\n", "/ext/out.txt", seconds=0)
        finally:
            scripts_mod.asyncio.sleep = real

    assert run(main()) == b"board said hi"


def test_run_and_read_empty_when_script_wrote_nothing():
    fs = FakeFilesystem()

    async def main():
        return await Flipper(FakeTransport(fs)).scripts.run_and_read("code\n", "/ext/out.txt", seconds=0)

    assert run(main()) == b""
