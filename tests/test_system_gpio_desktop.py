import asyncio
import datetime

import pytest

from flipperzero_ble import Flipper, FlipperError, Pin, PinMode
from flipperzero_ble._proto import flipper_pb2, gpio_pb2, system_pb2

from .fakes import FakeTransport, kinds, ok


def run(coro):
    return asyncio.run(coro)


def test_set_datetime_includes_weekday():
    transport = FakeTransport(lambda command: [ok()])

    async def main():
        await Flipper(transport).system.set_datetime(datetime.datetime(2026, 9, 29, 13, 45, 6))

    run(main())
    dt = transport.commands[0][0].system_set_datetime_request.datetime
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.weekday) == (2026, 9, 29, 13, 45, 6, 2)


def test_reboot_does_not_wait_for_a_reply():
    transport = FakeTransport(lambda command: None)

    async def main():
        await asyncio.wait_for(Flipper(transport).system.reboot("dfu"), 1)

    run(main())
    assert transport.sent_without_reply[0].system_reboot_request.mode == system_pb2.RebootRequest.DFU


def test_properties_skip_the_closing_empty_message():
    def handler(command):
        assert command[0].property_get_request.key == "devinfo.firmware"
        return [
            ok(has_next=True, property_get_response={"key": "firmware.version", "value": "1.4.3"}),
            ok(has_next=True, property_get_response={"key": "firmware.branch", "value": "release"}),
            ok(),
        ]

    async def main():
        return await Flipper(FakeTransport(handler)).system.properties("devinfo.firmware")

    assert run(main()) == {"firmware.version": "1.4.3", "firmware.branch": "release"}


def test_update_error_names_the_result_code():
    def handler(command):
        return [flipper_pb2.Main(
            command_status=flipper_pb2.ERROR_INVALID_PARAMETERS,
            system_update_response={"code": system_pb2.UpdateResponse.ManifestInvalid},
        )]

    async def main():
        with pytest.raises(FlipperError, match="ManifestInvalid"):
            await Flipper(FakeTransport(handler)).system.update("/ext/update/x/update.fuf")

    run(main())


def test_gpio_accepts_names_and_header_numbers():
    def handler(command):
        kind = command[0].WhichOneof("content")
        if kind == "gpio_read_pin":
            return [ok(gpio_read_pin_response={"value": 1})]
        if kind == "gpio_get_pin_mode":
            return [ok(gpio_get_pin_mode_response={"mode": gpio_pb2.INPUT})]
        if kind == "gpio_get_otg_mode":
            return [ok(gpio_get_otg_mode_response={"mode": gpio_pb2.ON})]
        return [ok()]

    transport = FakeTransport(handler)

    async def main():
        gpio = Flipper(transport).gpio
        await gpio.set_mode(2, "output")
        await gpio.write("pa7", True)
        await gpio.set_pull(Pin.PC0, "up")
        return await gpio.read("16"), await gpio.mode("PC0"), await gpio.otg()

    assert run(main()) == (True, PinMode.INPUT, True)
    assert transport.commands[0][0].gpio_set_pin_mode.pin == gpio_pb2.PA7
    assert transport.commands[1][0].gpio_write_pin.value == 1
    assert transport.commands[3][0].gpio_read_pin.pin == gpio_pb2.PC0
    assert Pin.PA7.header_number == 2


def test_gpio_rejects_non_gpio_header_pin():
    with pytest.raises(ValueError):
        run(Flipper(FakeTransport(lambda c: [ok()])).gpio.read(1))


def test_desktop_lock_state_and_changes():
    locked = {"value": True}

    def handler(command):
        if command[0].WhichOneof("content") == "desktop_is_locked_request":
            return [ok() if locked["value"] else flipper_pb2.Main(command_status=flipper_pb2.ERROR)]
        return [ok()]

    transport = FakeTransport(handler)

    async def main():
        flipper = Flipper(transport)
        states = [await flipper.desktop.is_locked()]
        locked["value"] = False
        states.append(await flipper.desktop.is_locked())
        async with flipper.desktop.lock_changes() as changes:
            transport.push(flipper_pb2.Main(desktop_status={"locked": True}))
            async for change in changes:
                states.append(change)
                break
        return states

    assert run(main()) == [True, False, True]
    assert kinds(transport)[-2:] == ["desktop_status_subscribe_request", "desktop_status_unsubscribe_request"]


def test_gpio_mode_of_unconfigured_pin_is_none():
    transport = FakeTransport(lambda c: [flipper_pb2.Main(command_status=flipper_pb2.ERROR_GPIO_UNKNOWN_PIN_MODE)])
    assert run(Flipper(transport).gpio.mode("PA7")) is None
