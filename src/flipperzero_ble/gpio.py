from enum import IntEnum
from typing import TYPE_CHECKING

from ._proto import gpio_pb2
from .errors import FlipperError

if TYPE_CHECKING:
    from .client import Flipper


class Pin(IntEnum):
    """GPIO pins on the Flipper's top header. `header_number` is the pin's position printed on the case."""

    PC0 = gpio_pb2.PC0
    PC1 = gpio_pb2.PC1
    PC3 = gpio_pb2.PC3
    PB2 = gpio_pb2.PB2
    PB3 = gpio_pb2.PB3
    PA4 = gpio_pb2.PA4
    PA6 = gpio_pb2.PA6
    PA7 = gpio_pb2.PA7

    @property
    def header_number(self) -> int:
        return _HEADER_NUMBERS[self]


_HEADER_NUMBERS = {Pin.PA7: 2, Pin.PA6: 3, Pin.PA4: 4, Pin.PB3: 5, Pin.PB2: 6, Pin.PC3: 7, Pin.PC1: 15, Pin.PC0: 16}


class PinMode(IntEnum):
    OUTPUT = gpio_pb2.OUTPUT
    INPUT = gpio_pb2.INPUT


class Pull(IntEnum):
    NONE = gpio_pb2.NO
    UP = gpio_pb2.UP
    DOWN = gpio_pb2.DOWN


def _pin(pin: Pin | str | int) -> Pin:
    """Accepts a Pin, its name ("PA7"), or its header number (2)."""
    if isinstance(pin, Pin):
        return pin
    if isinstance(pin, str) and not pin.isdigit():
        return Pin[pin.upper()]
    number = int(pin)
    for candidate, header_number in _HEADER_NUMBERS.items():
        if header_number == number:
            return candidate
    raise ValueError(f"Header pin {number} isn't a GPIO pin; use one of {sorted(_HEADER_NUMBERS.values())}")


class Gpio:
    """The GPIO pins on the Flipper's header, and the 5V supply on pin 1.

    Pins can be given as a Pin, a name ("PA7") or the header number (2). Pins are 3.3V; don't connect 5V to them.
    """

    def __init__(self, flipper: "Flipper"):
        self._flipper = flipper

    async def set_mode(self, pin: Pin | str | int, mode: PinMode | str) -> None:
        mode = PinMode[mode.upper()] if isinstance(mode, str) else mode
        await self._flipper.request(gpio_set_pin_mode={"pin": _pin(pin), "mode": mode})

    async def mode(self, pin: Pin | str | int) -> PinMode | None:
        """The pin's mode, or None if it hasn't been set to input or output (pins start unconfigured)."""
        try:
            parts = await self._flipper.request(gpio_get_pin_mode={"pin": _pin(pin)})
        except FlipperError as exc:
            if exc.status == "ERROR_GPIO_UNKNOWN_PIN_MODE":
                return None
            raise
        return PinMode(parts[0].gpio_get_pin_mode_response.mode)

    async def set_pull(self, pin: Pin | str | int, pull: Pull | str) -> None:
        """Sets an input's pull resistor: "up", "down" or "none"."""
        pull = Pull[pull.upper()] if isinstance(pull, str) else pull
        await self._flipper.request(gpio_set_input_pull={"pin": _pin(pin), "pull_mode": pull})

    async def read(self, pin: Pin | str | int) -> bool:
        """Reads an input pin (set it to input mode first). Raises FlipperError ERROR_GPIO_MODE_INCORRECT if not."""
        parts = await self._flipper.request(gpio_read_pin={"pin": _pin(pin)})
        return bool(parts[0].gpio_read_pin_response.value)

    async def write(self, pin: Pin | str | int, value: bool | int) -> None:
        """Drives an output pin high or low (set it to output mode first)."""
        await self._flipper.request(gpio_write_pin={"pin": _pin(pin), "value": int(bool(value))})

    async def otg(self) -> bool:
        """True if the 5V supply on header pin 1 is on."""
        parts = await self._flipper.request(gpio_get_otg_mode={})
        return parts[0].gpio_get_otg_mode_response.mode == gpio_pb2.ON

    async def set_otg(self, enabled: bool) -> None:
        """Turns the 5V supply on header pin 1 on or off."""
        await self._flipper.request(gpio_set_otg_mode={"mode": gpio_pb2.ON if enabled else gpio_pb2.OFF})
