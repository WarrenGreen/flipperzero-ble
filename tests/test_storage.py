import asyncio
import datetime

from flipperzero_ble import FileEntry, Flipper

from .fakes import FakeFilesystem, FakeTransport, kinds, ok


def run(coro):
    return asyncio.run(coro)


def test_list_passes_md5_and_size_filter():
    def handler(command):
        request = command[0].storage_list_request
        assert (request.include_md5, request.filter_max_size) == (True, 100)
        return [ok(storage_list_response={"file": [{"name": "a.sub", "size": 10, "md5sum": "abc"}]})]

    async def main():
        return await Flipper(FakeTransport(handler)).storage.list("/ext", include_md5=True, max_size=100)

    assert run(main()) == [FileEntry("a.sub", False, 10, "abc")]


def test_timestamp_is_naive_flipper_time():
    transport = FakeTransport(lambda command: [ok(storage_timestamp_response={"timestamp": 1_700_000_000})])

    async def main():
        return await Flipper(transport).storage.timestamp("/ext/a.sub")

    assert run(main()) == datetime.datetime(2023, 11, 14, 22, 13, 20)


def test_mkdir_parents_creates_missing_directories():
    fs = FakeFilesystem()

    async def main():
        flipper = Flipper(FakeTransport(fs))
        await flipper.storage.mkdir("/ext/a/b/c", parents=True)
        await flipper.storage.mkdir("/ext/a/b/c", parents=True)  # already exists: fine

    run(main())
    assert {"/ext/a", "/ext/a/b", "/ext/a/b/c"} <= fs.dirs


def test_walk_and_download_directory(tmp_path):
    fs = FakeFilesystem({
        "/ext/remote/one.txt": b"1",
        "/ext/remote/sub/two.txt": b"22",
        **{f"/ext/remote/many/{i}.txt": bytes([i]) for i in range(10)},
    })
    progress = []

    async def main():
        flipper = Flipper(FakeTransport(fs))
        walked = [(d, [e.name for e in dirs], [f.name for f in files]) async for d, dirs, files in flipper.storage.walk("/ext/remote")]
        await flipper.storage.download("/ext/remote", tmp_path / "copy", progress=lambda *p: progress.append(p))
        return walked

    walked = run(main())
    assert walked[0] == ("/ext/remote", ["many", "sub"], ["one.txt"])
    assert len(walked[1][2]) == 10  # listing spans two response pages
    assert (tmp_path / "copy" / "sub" / "two.txt").read_bytes() == b"22"
    assert (tmp_path / "copy" / "many" / "9.txt").read_bytes() == bytes([9])
    assert progress[-1] == (12, 12)


def test_upload_directory(tmp_path):
    (tmp_path / "src" / "nested").mkdir(parents=True)
    (tmp_path / "src" / "a.sub").write_bytes(b"A")
    (tmp_path / "src" / "nested" / "b.sub").write_bytes(b"B")
    fs = FakeFilesystem()

    async def main():
        flipper = Flipper(FakeTransport(fs))
        await flipper.storage.upload(tmp_path / "src", "/ext/subghz/mine")
        await flipper.storage.upload(tmp_path / "src" / "a.sub", "/ext/single.sub")

    run(main())
    assert fs.files == {"/ext/subghz/mine/a.sub": b"A", "/ext/subghz/mine/nested/b.sub": b"B", "/ext/single.sub": b"A"}


def test_text_helpers_and_is_dir():
    fs = FakeFilesystem()

    async def main():
        flipper = Flipper(FakeTransport(fs))
        await flipper.storage.write_text("/ext/note.txt", "héllo")
        return await flipper.storage.read_text("/ext/note.txt"), await flipper.storage.is_dir("/ext"), await flipper.storage.is_dir("/ext/note.txt")

    assert run(main()) == ("héllo", True, False)


def test_backup_restore_extract_send_paths():
    transport = FakeTransport(lambda command: [ok()])

    async def main():
        flipper = Flipper(transport)
        await flipper.storage.backup("/ext/int.tar")
        await flipper.storage.restore("/ext/int.tar")
        await flipper.storage.extract("/ext/pkg.tar", "/ext/pkg")

    run(main())
    assert kinds(transport) == ["storage_backup_create_request", "storage_backup_restore_request", "storage_mkdir_request", "storage_tar_extract_request"]
    assert transport.commands[3][0].storage_tar_extract_request.out_path == "/ext/pkg"


def test_extract_creates_the_destination_first():
    transport = FakeTransport(lambda command: [ok()])

    async def main():
        await Flipper(transport).storage.extract("/ext/pkg.tar", "/ext/out")

    run(main())
    assert kinds(transport)[-2:] == ["storage_mkdir_request", "storage_tar_extract_request"]
