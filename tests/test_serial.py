import asyncio

from flipperzero_ble import Flipper
from flipperzero_ble.serial import OUTPUT_PATH, SCRATCH_JS, build_script

from .fakes import FakeFilesystem, FakeTransport, kinds


def run(coro):
    return asyncio.run(coro)


def test_build_script_streams_to_file_without_buffering():
    js = build_script(b"help\r\n", baud=115200, reads=4, wake=True)
    # Never materializes the incoming bytes in JS (that overflows the ~2 KB runner stack).
    assert "Uint8Array" not in js and "toString(16)" not in js
    # Streams straight to the output file, and closes it.
    assert f'storage.openFile("{OUTPUT_PATH}", "w", "create_always")' in js
    assert js.count("raw.write(c)") == 4  # one per unrolled read; no for/while loop
    assert "for (" not in js and "while (" not in js
    assert 'serial.setup("usart", 115200)' in js
    # The payload is escaped as \xNN, so a CLI newline can't break the JS string literal.
    assert "\\x68\\x65\\x6c\\x70\\x0d\\x0a" in js  # "help\r\n"
    assert js.strip().endswith('print("done");')


def test_build_script_wake_and_no_wake():
    assert 'serial.write("\\r\\n")' in build_script(b"x", 9600, 1, wake=True)
    assert 'serial.write("\\r\\n")' not in build_script(b"x", 9600, 1, wake=False)


def test_exchange_returns_the_captured_file_and_cleans_up():
    fs = FakeFilesystem()

    async def main():
        flipper = Flipper(FakeTransport(fs))

        # The fake JS Runner can't run JS, so emulate the script writing its output file while it's "running".
        async def fake_sleep(_):
            fs.files[OUTPUT_PATH] = b"#help\n== Commands ==\nchannel\n"

        import flipperzero_ble.serial as serial_mod
        real = serial_mod.asyncio.sleep
        serial_mod.asyncio.sleep = fake_sleep
        try:
            return await flipper.serial.command("help", read_seconds=1.0)
        finally:
            serial_mod.asyncio.sleep = real

    reply = run(main())
    assert reply == "#help\n== Commands ==\nchannel\n"
    assert OUTPUT_PATH not in fs.files  # cleaned up
    assert SCRATCH_JS not in fs.files  # scratch script removed by the session


def test_exchange_uploads_script_starts_js_runner_and_reads_empty_when_silent():
    fs = FakeFilesystem()
    transport = FakeTransport(fs)

    async def main():
        # No output file is ever written (device silent) -> b"".
        return await Flipper(transport).serial.exchange(b"AT\r\n", read_seconds=0.4)

    assert run(main()) == b""
    seen = kinds(transport)
    assert "storage_write_request" in seen  # the relay script was uploaded
    assert "app_start_request" in seen  # the JS Runner was launched
    assert transport.commands[[c[0].WhichOneof("content") for c in transport.commands].index("app_start_request")][0].app_start_request.name == "js_app"
