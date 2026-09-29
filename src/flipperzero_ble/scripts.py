"""Running JavaScript on the Flipper's built-in JS runner.

The RPC protocol has no command for scripts, so this drives the JS Runner app: it uploads a `.js` file, starts
the app on it, and (because the runner has no "finished" signal over BLE) leaves it running for a set time
before pressing Back to close it. A script has no return value here, so to get data back, have it write a file
and read that with `flipper.storage`. This is the way to reach hardware the RPC protocol can't, such as a
module on the GPIO serial pins (see `examples/serial_relay.js`).

Scripts run on the Flipper's small embedded JS engine (mJS), which is quirky: prefer `while` loops over `for`,
and flush files as you go (open/append/close) rather than relying on one close at the end.
"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from .apps import AppSession

if TYPE_CHECKING:
    from .client import Flipper

APP_NAME = "js_app"
SCRATCH_PATH = "/ext/.flipperzero_ble_script.js"


class ScriptSession(AppSession):
    """The JS Runner open on a script. Leaving the block closes the runner."""

    def __init__(self, flipper: "Flipper", path: str, scratch: bool):
        super().__init__(flipper, path)  # the runner is started with the script path as its name/argument
        self._path = path
        self._scratch = scratch

    async def __aenter__(self) -> "ScriptSession":
        # The JS Runner is launched by app name with the script path as the argument, not via apps.start(path).
        self._states = self.apps.states()
        try:
            await self.apps.start(APP_NAME, self._path)
        except BaseException:
            self._states.close()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            if self._flipper.connected:
                # The runner isn't in RPC mode, so Back closes it; a second Back leaves any result popup.
                await self._flipper.gui.press("back")
                await self._flipper.gui.press("back")
                await self._wait_until_unlocked(3.0)
        finally:
            self._states.close()
            if self._scratch and self._flipper.connected:
                await self._flipper.storage.delete(self._path, missing_ok=True)


class Scripts:
    """Runs `.js` files on the Flipper's JS Runner."""

    def __init__(self, flipper: "Flipper"):
        self._flipper = flipper

    async def _upload(self, source: str | bytes | Path, remote_path: str | None) -> tuple[str, bool]:
        """Uploads a local .js file (Path) or inline script text (str/bytes). Returns (remote path, scratch?)."""
        if isinstance(source, Path):
            data, default = source.read_bytes(), f"/ext/apps/Scripts/{source.name}"
        else:
            data, default = (source if isinstance(source, bytes) else source.encode()), SCRATCH_PATH
        remote = remote_path or default
        await self._flipper.storage.write(remote, data)
        return remote, remote_path is None and remote == SCRATCH_PATH

    def session(self, remote_path: str) -> ScriptSession:
        """Runs a `.js` file that's already on the Flipper, closing the runner when the block ends."""
        return ScriptSession(self._flipper, remote_path, scratch=False)

    async def run(self, source: str | bytes | Path, seconds: float = 5.0, remote_path: str | None = None) -> None:
        """Runs a script for `seconds`, then closes the runner.

        `source` is a path to a `.js` file, or the script text or bytes (uploaded to a scratch file). Scripts
        return nothing here; to get data back, have the script write a file and read it with `flipper.storage`.
        """
        path, scratch = await self._upload(source, remote_path)
        async with ScriptSession(self._flipper, path, scratch):
            await asyncio.sleep(seconds)

    async def run_and_read(
        self,
        source: str | bytes | Path,
        output_path: str,
        seconds: float = 5.0,
        remote_path: str | None = None,
    ) -> bytes:
        """Runs a script that writes `output_path`, then returns that file's contents (b"" if it wasn't written).

        The script must finish writing within `seconds`. Have it flush as it goes (open/append/close), because
        the runner is closed by force and won't flush a file left open.
        """
        path, scratch = await self._upload(source, remote_path)
        await self._flipper.storage.delete(output_path, missing_ok=True)
        async with ScriptSession(self._flipper, path, scratch):
            await asyncio.sleep(seconds)
        if await self._flipper.storage.exists(output_path):
            data = await self._flipper.storage.read(output_path)
            await self._flipper.storage.delete(output_path, missing_ok=True)
            return data
        return b""
