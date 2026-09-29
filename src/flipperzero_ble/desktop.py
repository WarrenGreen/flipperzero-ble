import contextlib
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from .errors import FlipperError

if TYPE_CHECKING:
    from .client import Flipper


class Desktop:
    """The Flipper's lock screen."""

    def __init__(self, flipper: "Flipper"):
        self._flipper = flipper

    async def is_locked(self) -> bool:
        # The Flipper answers OK when locked and a bare ERROR when unlocked.
        try:
            await self._flipper.request(desktop_is_locked_request={})
        except FlipperError as exc:
            if exc.status == "ERROR":
                return False
            raise
        return True

    async def unlock(self) -> None:
        """Unlocks the Flipper. A PIN lock can't be bypassed this way; the Flipper still asks for the PIN."""
        await self._flipper.request(desktop_unlock_request={})

    @contextlib.asynccontextmanager
    async def lock_changes(self) -> AsyncIterator[AsyncIterator[bool]]:
        """`async with flipper.desktop.lock_changes() as changes: async for locked in changes: ...`"""
        async with self._flipper.stream(
            "desktop_status",
            start={"desktop_status_subscribe_request": {}},
            stop={"desktop_status_unsubscribe_request": {}},
        ) as statuses:
            yield (status.locked async for status in statuses)
