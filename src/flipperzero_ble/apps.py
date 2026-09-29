import asyncio
from enum import IntEnum
from typing import TYPE_CHECKING

from ._proto import application_pb2
from .errors import AppError, FlipperError

if TYPE_CHECKING:
    from .client import Flipper, Subscription


class AppState(IntEnum):
    CLOSED = application_pb2.APP_CLOSED
    STARTED = application_pb2.APP_STARTED


class Apps:
    """Launches apps and drives apps that support RPC control (Sub-GHz, Infrared, NFC, RFID, iButton).

    For the RPC-controlled apps, `session(name)` is usually easier: it starts the app, waits until it's
    ready, and exits it afterwards. Commands an app rejects raise AppError with the app's own explanation.
    """

    def __init__(self, flipper: "Flipper"):
        self._flipper = flipper

    async def start(self, name: str, args: str = "") -> None:
        """Starts an app by name, e.g. `start("Sub-GHz", "RPC")`. Pass args="RPC" for remote control mode.

        Built-in apps: "Sub-GHz", "Infrared", "NFC", "125 kHz RFID", "iButton", "GPIO", "Bad USB", "U2F",
        "Archive". An installed .fap can be started by its path, e.g. "/ext/apps/Games/snake_game.fap".
        """
        try:
            await self._flipper.request(app_start_request={"name": name, "args": args})
        except FlipperError as exc:
            if exc.status == "ERROR_APP_SYSTEM_LOCKED":
                raise FlipperError(
                    exc.status, "Another app is open on the Flipper; return it to the home screen first."
                ) from exc
            if exc.status == "ERROR_INVALID_PARAMETERS":
                raise FlipperError(exc.status, f"The Flipper has no app named {name!r}.") from exc
            raise

    async def exit(self) -> None:
        """Asks the app running in RPC mode to close."""
        await self._command(app_exit_request={})

    async def is_locked(self) -> bool:
        """True while an app is running, which blocks starting another one."""
        parts = await self._flipper.request(app_lock_status_request={})
        return parts[0].app_lock_status_response.locked

    async def load_file(self, path: str) -> None:
        """Opens a file in the running app, e.g. a .sub, .ir, .nfc, .rfid or .ibtn file."""
        await self._command(app_load_file_request={"path": path})

    async def press(self, args: str = "", index: int = 0) -> None:
        """Presses the app's button, named by `args` or by `index` (e.g. an Infrared remote's button)."""
        await self._command(app_button_press_request={"args": args, "index": index})

    async def release(self) -> None:
        await self._command(app_button_release_request={})

    async def press_release(self, args: str = "", index: int = 0) -> None:
        """Presses and releases the app's button in one command (sends an Infrared signal once)."""
        await self._command(app_button_press_release_request={"args": args, "index": index})

    async def send_data(self, data: bytes) -> None:
        """Sends raw bytes to the running app. Only apps built to exchange data understand them."""
        await self._command(app_data_exchange_request={"data": data})

    def data(self, maxsize: int = 0) -> "Subscription":
        """Collects bytes the running app sends. Iterate it (`async for message in ...`), each item has `.data`."""
        return self._flipper.subscribe("app_data_exchange_request", maxsize)

    def states(self) -> "Subscription":
        """Collects APP_STARTED/APP_CLOSED notices from apps in RPC mode. Each item has `.state`."""
        return self._flipper.subscribe("app_state_response")

    async def last_error(self) -> tuple[int, str]:
        """The (code, text) the running app gave for its last failed command, or (0, "")."""
        parts = await self._flipper.request(app_get_error_request={})
        error = parts[0].app_get_error_response
        return error.code, error.text

    def session(self, name: str, start_timeout: float = 5.0) -> "AppSession":
        return AppSession(self._flipper, name, start_timeout)

    async def _command(self, **content) -> None:
        try:
            await self._flipper.request(**content)
        except FlipperError as exc:
            if exc.status != "ERROR_APP_CMD_ERROR":
                raise
            code, text = await self.last_error()
            raise AppError(exc.status, code, text) from exc


class AppSession:
    """Keeps an app open in RPC mode for a series of commands.

    Entering starts the app and waits for it to report it's ready; leaving exits it and waits for it to close,
    so another app can start straight away.
    """

    def __init__(self, flipper: "Flipper", name: str, start_timeout: float = 5.0):
        self._flipper = flipper
        self.name = name
        self._start_timeout = start_timeout
        self._states: Subscription | None = None

    @property
    def apps(self) -> Apps:
        return self._flipper.apps

    async def __aenter__(self):
        self._states = self.apps.states()
        try:
            await self.apps.start(self.name, "RPC")
            await self._wait_for(AppState.STARTED, self._start_timeout)
        except BaseException:
            self._states.close()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            if self._flipper.connected:
                try:
                    await self.apps.exit()
                except FlipperError:
                    pass  # the app already closed, e.g. someone pressed Back on the Flipper
                else:
                    await self._wait_for(AppState.CLOSED, 5.0)
        finally:
            self._states.close()

    async def _wait_for(self, state: AppState, timeout: float) -> None:
        # Firmware that predates app state notices never sends one, so give up quietly after the timeout.
        deadline = asyncio.get_running_loop().time() + timeout
        while (remaining := deadline - asyncio.get_running_loop().time()) > 0:
            try:
                notice = await self._states.get(timeout=remaining)
            except (TimeoutError, asyncio.TimeoutError):
                return
            if notice.state == state:
                return

    async def load_file(self, path: str) -> None:
        await self.apps.load_file(path)

    async def press(self, args: str = "", index: int = 0) -> None:
        await self.apps.press(args, index)

    async def release(self) -> None:
        await self.apps.release()

    async def press_release(self, args: str = "", index: int = 0) -> None:
        await self.apps.press_release(args, index)

    async def send_data(self, data: bytes) -> None:
        await self.apps.send_data(data)

