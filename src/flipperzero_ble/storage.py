import posixpath
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._proto import storage_pb2
from .errors import FlipperError

if TYPE_CHECKING:
    from .client import Flipper

WRITE_CHUNK_SIZE = 512


@dataclass(frozen=True)
class FileEntry:
    name: str
    is_dir: bool
    size: int


def _entry(file: storage_pb2.File, fallback_name: str = "") -> FileEntry:
    return FileEntry(file.name or fallback_name, file.type == storage_pb2.File.DIR, file.size)


class Storage:
    """SD card (`/ext`) and internal (`/int`) storage."""

    def __init__(self, flipper: "Flipper"):
        self._flipper = flipper

    async def list(self, path: str) -> list[FileEntry]:
        parts = await self._flipper.request(storage_list_request={"path": path})
        return [_entry(f) for part in parts for f in part.storage_list_response.file]

    async def read(self, path: str) -> bytes:
        parts = await self._flipper.request(storage_read_request={"path": path}, timeout=120.0)
        return b"".join(part.storage_read_response.file.data for part in parts)

    async def write(self, path: str, data: bytes) -> None:
        chunks = [data[i : i + WRITE_CHUNK_SIZE] for i in range(0, len(data), WRITE_CHUNK_SIZE)] or [b""]
        await self._flipper.request_stream(
            ({"storage_write_request": {"path": path, "file": {"data": chunk}}} for chunk in chunks),
            timeout=120.0,
        )

    async def stat(self, path: str) -> FileEntry | None:
        try:
            parts = await self._flipper.request(storage_stat_request={"path": path})
        except FlipperError as exc:
            if exc.status == "ERROR_STORAGE_NOT_EXIST":
                return None
            raise
        return _entry(parts[0].storage_stat_response.file, posixpath.basename(path))

    async def exists(self, path: str) -> bool:
        return await self.stat(path) is not None

    async def mkdir(self, path: str, exist_ok: bool = False) -> None:
        try:
            await self._flipper.request(storage_mkdir_request={"path": path})
        except FlipperError as exc:
            if not (exist_ok and exc.status == "ERROR_STORAGE_EXIST"):
                raise

    async def delete(self, path: str, recursive: bool = False, missing_ok: bool = False) -> None:
        try:
            await self._flipper.request(storage_delete_request={"path": path, "recursive": recursive})
        except FlipperError as exc:
            if not (missing_ok and exc.status == "ERROR_STORAGE_NOT_EXIST"):
                raise

    async def rename(self, old_path: str, new_path: str) -> None:
        await self._flipper.request(storage_rename_request={"old_path": old_path, "new_path": new_path})

    async def md5(self, path: str) -> str:
        parts = await self._flipper.request(storage_md5sum_request={"path": path})
        return parts[0].storage_md5sum_response.md5sum

    async def space(self, path: str = "/ext") -> tuple[int, int]:
        """Returns (total_bytes, free_bytes)."""
        parts = await self._flipper.request(storage_info_request={"path": path})
        info = parts[0].storage_info_response
        return info.total_space, info.free_space
