from __future__ import annotations

import contextlib
import datetime
import os
import posixpath
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ._proto import storage_pb2
from .errors import FlipperError

if TYPE_CHECKING:
    from .client import Flipper

WRITE_CHUNK_SIZE = 512
TIMEOUT = 30.0

Progress = Callable[[int, int], None]
"""Called with (bytes done, total bytes) as a transfer progresses."""


@dataclass(frozen=True)
class FileEntry:
    name: str
    is_dir: bool
    size: int
    md5: str = ""


def _entry(file: storage_pb2.File, fallback_name: str = "") -> FileEntry:
    return FileEntry(file.name or fallback_name, file.type == storage_pb2.File.DIR, file.size, file.md5sum)


class Storage:
    """SD card (`/ext`) and internal (`/int`) storage. Paths are absolute, e.g. "/ext/subghz/Garage.sub"."""

    def __init__(self, flipper: "Flipper"):
        self._flipper = flipper

    async def list(self, path: str, include_md5: bool = False, max_size: int = 0) -> list[FileEntry]:
        """Lists a directory. `include_md5` fills in each file's `md5`; `max_size` skips larger files.

        The Flipper leaves out names that aren't plain ASCII. Listing "/" gives the storage roots.
        """
        parts = await self._flipper.request(
            storage_list_request={"path": path, "include_md5": include_md5, "filter_max_size": max_size}
        )
        return [_entry(f) for part in parts for f in part.storage_list_response.file]

    async def walk(self, top: str) -> AsyncIterator[tuple[str, list[FileEntry], list[FileEntry]]]:
        """Like os.walk: yields (directory path, subdirectories, files) for `top` and everything below it."""
        entries = await self.list(top)
        dirs = [e for e in entries if e.is_dir]
        yield top, dirs, [e for e in entries if not e.is_dir]
        for d in dirs:
            async for item in self.walk(posixpath.join(top, d.name)):
                yield item

    async def read(self, path: str, progress: Progress | None = None) -> bytes:
        total = (await self._stat_or_raise(path)).size if progress else 0
        data = bytearray()
        async with contextlib.aclosing(
            self._flipper.iter_request([{"storage_read_request": {"path": path}}], timeout=TIMEOUT)
        ) as parts:
            async for part in parts:
                data += part.storage_read_response.file.data
                if progress:
                    progress(len(data), total)
        return bytes(data)

    async def read_text(self, path: str, encoding: str = "utf-8") -> str:
        return (await self.read(path)).decode(encoding)

    async def write(self, path: str, data: bytes, progress: Progress | None = None) -> None:
        """Creates or replaces a file. Parent directories must already exist (see mkdir(parents=True))."""
        chunks = [data[i : i + WRITE_CHUNK_SIZE] for i in range(0, len(data), WRITE_CHUNK_SIZE)] or [b""]
        await self._flipper.request_stream(
            ({"storage_write_request": {"path": path, "file": {"data": chunk}}} for chunk in chunks),
            timeout=TIMEOUT,
            on_sent=progress and (lambda i: progress(min((i + 1) * WRITE_CHUNK_SIZE, len(data)), len(data))),
        )

    async def write_text(self, path: str, text: str, encoding: str = "utf-8") -> None:
        await self.write(path, text.encode(encoding))

    async def stat(self, path: str) -> FileEntry | None:
        """The file or directory at `path`, or None if there's nothing there."""
        try:
            return await self._stat_or_raise(path)
        except FlipperError as exc:
            if exc.status == "ERROR_STORAGE_NOT_EXIST":
                return None
            raise

    async def _stat_or_raise(self, path: str) -> FileEntry:
        parts = await self._flipper.request(storage_stat_request={"path": path})
        return _entry(parts[0].storage_stat_response.file, posixpath.basename(path))

    async def exists(self, path: str) -> bool:
        return await self.stat(path) is not None

    async def is_dir(self, path: str) -> bool:
        entry = await self.stat(path)
        return entry is not None and entry.is_dir

    async def timestamp(self, path: str) -> datetime.datetime:
        """When a file was last modified, as a naive datetime in the Flipper clock's time zone."""
        parts = await self._flipper.request(storage_timestamp_request={"path": path})
        seconds = parts[0].storage_timestamp_response.timestamp
        return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).replace(tzinfo=None)

    async def mkdir(self, path: str, exist_ok: bool = False, parents: bool = False) -> None:
        if parents:
            parent = posixpath.dirname(path.rstrip("/"))
            if parent.count("/") > 1 and not await self.exists(parent):
                await self.mkdir(parent, exist_ok=True, parents=True)
        try:
            await self._flipper.request(storage_mkdir_request={"path": path})
        except FlipperError as exc:
            if not ((exist_ok or parents) and exc.status == "ERROR_STORAGE_EXIST"):
                raise

    async def delete(self, path: str, recursive: bool = False, missing_ok: bool = False) -> None:
        try:
            await self._flipper.request(storage_delete_request={"path": path, "recursive": recursive})
        except FlipperError as exc:
            if not (missing_ok and exc.status == "ERROR_STORAGE_NOT_EXIST"):
                raise

    async def rename(self, old_path: str, new_path: str) -> None:
        """Moves or renames a file or directory. An existing file at `new_path` is replaced."""
        await self._flipper.request(storage_rename_request={"old_path": old_path, "new_path": new_path})

    async def md5(self, path: str) -> str:
        parts = await self._flipper.request(storage_md5sum_request={"path": path})
        return parts[0].storage_md5sum_response.md5sum

    async def space(self, path: str = "/ext") -> tuple[int, int]:
        """Returns (total_bytes, free_bytes)."""
        parts = await self._flipper.request(storage_info_request={"path": path})
        info = parts[0].storage_info_response
        return info.total_space, info.free_space

    async def upload(self, local: str | os.PathLike, remote: str, progress: Progress | None = None) -> None:
        """Copies a local file, or a directory recursively, to `remote` on the Flipper.

        For directories, `progress` is called with (files done, total files).
        """
        local = Path(local)
        if not local.is_dir():
            await self.write(remote, local.read_bytes(), progress)
            return
        files = sorted(p for p in local.rglob("*") if p.is_file())
        await self.mkdir(remote, parents=True)
        created = {remote}
        for i, file in enumerate(files):
            target = posixpath.join(remote, *file.relative_to(local).parts)
            if posixpath.dirname(target) not in created:
                await self.mkdir(posixpath.dirname(target), parents=True)
                created.add(posixpath.dirname(target))
            await self.write(target, file.read_bytes())
            if progress:
                progress(i + 1, len(files))

    async def download(self, remote: str, local: str | os.PathLike, progress: Progress | None = None) -> None:
        """Copies a file, or a directory recursively, from the Flipper to `local`.

        For directories, `progress` is called with (files done, total files found so far).
        """
        local = Path(local)
        if not await self.is_dir(remote):
            local.write_bytes(await self.read(remote, progress))
            return
        done = found = 0
        async for directory, _, files in self.walk(remote):
            target_dir = local.joinpath(*posixpath.relpath(directory, remote).split("/"))
            target_dir.mkdir(parents=True, exist_ok=True)
            found += len(files)
            for file in files:
                (target_dir / file.name).write_bytes(await self.read(posixpath.join(directory, file.name)))
                done += 1
                if progress:
                    progress(done, found)

    async def extract(self, tar_path: str, out_path: str) -> None:
        """Unpacks a .tar archive on the Flipper into the directory `out_path`, creating it if needed.

        The Flipper doesn't create folders for files on its own, so the archive must list each folder before
        the files in it, as the `tar` command does. Raises ERROR_STORAGE_INTERNAL otherwise.
        """
        await self.mkdir(out_path, parents=True)
        await self._flipper.request(storage_tar_extract_request={"tar_path": tar_path, "out_path": out_path})

    async def backup(self, archive_path: str) -> None:
        """Archives internal storage (/int: settings, Bluetooth pairings, ...) to a .tar file, e.g. on /ext."""
        await self._flipper.request(storage_backup_create_request={"archive_path": archive_path}, timeout=120.0)

    async def restore(self, archive_path: str) -> None:
        """Restores internal storage from an archive made by backup()."""
        await self._flipper.request(storage_backup_restore_request={"archive_path": archive_path}, timeout=120.0)
