from typing import TYPE_CHECKING

from .errors import FlipperError

if TYPE_CHECKING:
    from .client import Flipper


class Apps:
    """Launches apps and drives apps that support RPC control (Sub-GHz, Infrared, NFC, RFID, iButton)."""

    def __init__(self, flipper: "Flipper"):
        self._flipper = flipper

    async def start(self, name: str, args: str = "") -> None:
        """Starts an app by name, e.g. `start("Sub-GHz", "RPC")`. Pass args="RPC" for remote control mode."""
        try:
            await self._flipper.request(app_start_request={"name": name, "args": args})
        except FlipperError as exc:
            if exc.status == "ERROR_APP_SYSTEM_LOCKED":
                raise FlipperError(
                    exc.status, "Another app is open on the Flipper; return it to the home screen first."
                ) from exc
            raise

    async def exit(self) -> None:
        await self._flipper.request(app_exit_request={})

    async def is_locked(self) -> bool:
        """True while an app is running, which blocks starting another one."""
        parts = await self._flipper.request(app_lock_status_request={})
        return parts[0].app_lock_status_response.locked

    async def load_file(self, path: str) -> None:
        await self._flipper.request(app_load_file_request={"path": path})

    async def press(self, args: str = "", index: int = 0) -> None:
        await self._flipper.request(app_button_press_request={"args": args, "index": index})

    async def release(self) -> None:
        await self._flipper.request(app_button_release_request={})
