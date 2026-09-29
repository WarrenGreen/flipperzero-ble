import asyncio
import time

import pytest

from flipperzero_ble import AppError, Flipper, FlipperError
from flipperzero_ble._proto import flipper_pb2
from flipperzero_ble.infrared import parse, parsed_signal, raw_signal, remote_file

from .fakes import FakeTransport, kinds, ok


def run(coro):
    return asyncio.run(coro)


def test_rejected_app_command_raises_app_error_with_explanation():
    def handler(command):
        if command[0].WhichOneof("content") == "app_load_file_request":
            return [flipper_pb2.Main(command_status=flipper_pb2.ERROR_APP_CMD_ERROR)]
        if command[0].WhichOneof("content") == "app_get_error_request":
            return [ok(app_get_error_response={"code": 1, "text": "Cannot parse file"})]
        return [ok()]

    async def main():
        with pytest.raises(AppError) as exc:
            await Flipper(FakeTransport(handler)).apps.load_file("/ext/subghz/bad.sub")
        return exc.value

    error = run(main())
    assert (error.status, error.code, error.text) == ("ERROR_APP_CMD_ERROR", 1, "Cannot parse file")
    assert "Cannot parse file" in str(error)


def test_unknown_app_name_is_explained():
    transport = FakeTransport(lambda command: [flipper_pb2.Main(command_status=flipper_pb2.ERROR_INVALID_PARAMETERS)])

    async def main():
        with pytest.raises(FlipperError, match="no app named 'Nope'"):
            await Flipper(transport).apps.start("Nope")

    run(main())


def test_session_waits_for_app_state_not_a_fixed_delay():
    transport = FakeTransport(lambda command: [ok()])

    async def main():
        start = time.monotonic()
        async with Flipper(transport).apps.session("Infrared") as session:
            await session.press_release("Power")
        return time.monotonic() - start

    assert run(main()) < 0.5
    assert kinds(transport) == ["app_start_request", "app_button_press_release_request", "app_exit_request", "app_lock_status_request"]


def test_session_exit_tolerates_app_already_closed():
    def handler(command):
        if command[0].WhichOneof("content") == "app_exit_request":
            return [flipper_pb2.Main(command_status=flipper_pb2.ERROR_APP_NOT_RUNNING)]
        return [ok()]

    async def main():
        start = time.monotonic()
        async with Flipper(FakeTransport(handler)).apps.session("NFC"):
            pass
        return time.monotonic() - start

    assert run(main()) < 0.5


def test_data_exchange_both_ways():
    transport = FakeTransport(lambda command: [ok()])

    async def main():
        flipper = Flipper(transport)
        with flipper.apps.data() as incoming:
            await flipper.apps.send_data(b"ping")
            transport.push(flipper_pb2.Main(app_data_exchange_request={"data": b"pong"}))
            return (await incoming.get(timeout=1)).data

    assert run(main()) == b"pong"
    assert transport.commands[0][0].app_data_exchange_request.data == b"ping"


def test_infrared_remote_file_round_trip():
    signals = [parsed_signal("Power", "NEC", 0x07, 0x02), raw_signal("Vol_up", [9000, 4500, 560])]
    contents = remote_file(signals)
    assert b"address: 07 00 00 00" in contents
    parsed = parse(contents)
    assert [(s.name, s.type, s.protocol) for s in parsed] == [("Power", "parsed", "NEC"), ("Vol_up", "raw", None)]
    assert parsed[1].fields["data"] == "9000 4500 560"


def test_infrared_send_by_name_and_index():
    transport = FakeTransport(lambda command: [ok()])

    async def main():
        flipper = Flipper(transport)
        await flipper.infrared.send("/ext/infrared/TV.ir", "Power")
        async with flipper.infrared.session() as ir:
            await ir.load("/ext/infrared/TV.ir")
            await ir.send(2, seconds=0)

    run(main())
    assert kinds(transport) == [
        "app_start_request", "app_load_file_request", "app_button_press_release_request", "app_exit_request", "app_lock_status_request",
        "app_start_request", "app_load_file_request", "app_button_press_request", "app_button_release_request",
        "app_exit_request", "app_lock_status_request",
    ]
    assert transport.commands[0][0].app_start_request.name == "Infrared"
    assert transport.commands[2][0].app_button_press_release_request.args == "Power"
    assert transport.commands[7][0].app_button_press_request.index == 2


def test_infrared_send_signal_uses_scratch_file_and_cleans_up():
    transport = FakeTransport(lambda command: [ok()])

    async def main():
        await Flipper(transport).infrared.send_signal(parsed_signal("Power", "NEC", 0x07, 0x02))

    run(main())
    assert kinds(transport) == [
        "app_start_request", "storage_write_request", "app_load_file_request",
        "app_button_press_release_request", "app_exit_request", "app_lock_status_request", "storage_delete_request",
    ]


@pytest.mark.parametrize("attr, app", [("nfc", "NFC"), ("rfid", "125 kHz RFID"), ("ibutton", "iButton")])
def test_emulation_loads_file_then_exits(attr, app):
    transport = FakeTransport(lambda command: [ok()])

    async def main():
        await getattr(Flipper(transport), attr).emulate_for("/ext/card", seconds=0)

    run(main())
    assert kinds(transport) == ["app_start_request", "app_load_file_request", "app_exit_request", "app_lock_status_request"]
    assert transport.commands[0][0].app_start_request.name == app
    assert transport.commands[0][0].app_start_request.args == "RPC"


def test_emulation_exits_app_when_file_fails_to_load():
    def handler(command):
        kind = command[0].WhichOneof("content")
        if kind == "app_load_file_request":
            return [flipper_pb2.Main(command_status=flipper_pb2.ERROR_APP_CMD_ERROR)]
        if kind == "app_get_error_request":
            return [ok(app_get_error_response={"code": 1, "text": "Cannot load key file"})]
        return [ok()]

    transport = FakeTransport(handler)

    async def main():
        with pytest.raises(AppError):
            async with Flipper(transport).rfid.emulate("/ext/lfrfid/bad.rfid"):
                pass

    run(main())
    assert kinds(transport)[-2:] == ["app_exit_request", "app_lock_status_request"]


def test_session_exit_waits_until_the_flipper_unlocks():
    lock_checks = []

    def handler(command):
        if command[0].WhichOneof("content") == "app_lock_status_request":
            lock_checks.append(True)
            return [ok(app_lock_status_response={"locked": len(lock_checks) < 3})]
        return [ok()]

    transport = FakeTransport(handler)

    async def main():
        async with Flipper(transport).apps.session("Infrared"):
            pass

    run(main())
    assert kinds(transport)[-3:] == ["app_lock_status_request"] * 3
