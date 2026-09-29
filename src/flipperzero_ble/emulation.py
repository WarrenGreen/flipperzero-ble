"""Emulating saved NFC tags, 125 kHz RFID cards and iButton keys. Loading a file in these apps' RPC mode
starts emulation; it runs until the app exits."""

import asyncio
from typing import TYPE_CHECKING

from .apps import AppSession

if TYPE_CHECKING:
    from .client import Flipper


class Emulation(AppSession):
    """Emulates one saved file while the `async with` block runs."""

    def __init__(self, flipper: "Flipper", app_name: str, path: str):
        super().__init__(flipper, app_name)
        self.path = path

    async def __aenter__(self) -> "Emulation":
        await super().__aenter__()
        try:
            await self.load_file(self.path)
        except BaseException:
            await super().__aexit__(None, None, None)
            raise
        return self


class _Emulator:
    APP_NAME = ""
    DIRECTORY = ""
    EXTENSION = ""

    def __init__(self, flipper: "Flipper"):
        self._flipper = flipper

    def emulate(self, path: str) -> Emulation:
        """`async with flipper.nfc.emulate("/ext/nfc/Card.nfc"):` emulates until the block ends."""
        return Emulation(self._flipper, self.APP_NAME, path)

    async def emulate_for(self, path: str, seconds: float) -> None:
        async with self.emulate(path):
            await asyncio.sleep(seconds)

    async def saved(self) -> list[str]:
        """Paths of the files saved in this app's folder on the SD card."""
        entries = await self._flipper.storage.list(self.DIRECTORY)
        return [
            f"{self.DIRECTORY}/{entry.name}"
            for entry in entries
            if not entry.is_dir and entry.name.endswith(self.EXTENSION)
        ]


class Nfc(_Emulator):
    """Emulates saved NFC tags (`.nfc` files)."""

    APP_NAME = "NFC"
    DIRECTORY = "/ext/nfc"
    EXTENSION = ".nfc"


class Rfid(_Emulator):
    """Emulates saved 125 kHz RFID cards (`.rfid` files)."""

    APP_NAME = "125 kHz RFID"
    DIRECTORY = "/ext/lfrfid"
    EXTENSION = ".rfid"


class IButton(_Emulator):
    """Emulates saved iButton keys (`.ibtn` files)."""

    APP_NAME = "iButton"
    DIRECTORY = "/ext/ibutton"
    EXTENSION = ".ibtn"
