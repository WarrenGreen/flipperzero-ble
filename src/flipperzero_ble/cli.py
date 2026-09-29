import argparse
import asyncio
import os
import posixpath
import sys
from pathlib import Path

from .client import Flipper, connect
from .discovery import scan
from .errors import FlipperDisconnectedError, FlipperError, FlipperNotFoundError
from .gpio import Pin
from .gui import Key
from .system import RebootMode

EMULATORS = ("nfc", "rfid", "ibutton")


async def cmd_scan(args) -> None:
    flippers = await scan(timeout=args.timeout)
    if not flippers:
        print("No Flippers found.")
    for f in flippers:
        print(f"{f.rssi:>4} dBm  {f.address}  {f.name}")


async def cmd_info(flipper: Flipper, args) -> None:
    info = await flipper.system.device_info()
    power = await flipper.system.power_info()
    for key, value in {**info, **power}.items() if args.all else _summary(info, power):
        print(f"{key}: {value}")


def _summary(info: dict, power: dict) -> list[tuple[str, str]]:
    wanted = [
        ("Name", info.get("hardware_name")),
        ("Firmware", f"{info.get('firmware_version', '?')} ({info.get('firmware_commit', '?')}, "
                     f"{info.get('firmware_build_date', '?')})"),
        ("Hardware", f"f{info.get('hardware_target', '?')} rev {info.get('hardware_ver', '?')}"),
        ("Battery", f"{power.get('charge_level', '?')}% ({power.get('charge_state', '?')})"),
    ]
    return [(k, v) for k, v in wanted if v]


async def cmd_props(flipper: Flipper, args) -> None:
    for key, value in (await flipper.system.properties(args.key)).items():
        print(f"{key}: {value}")


async def cmd_ping(flipper: Flipper, args) -> None:
    print(await flipper.system.ping(b"ping"))


async def cmd_alert(flipper: Flipper, args) -> None:
    await flipper.system.alert()


async def cmd_time(flipper: Flipper, args) -> None:
    if args.sync:
        await flipper.system.set_datetime()
    print(await flipper.system.datetime())


async def cmd_reboot(flipper: Flipper, args) -> None:
    await flipper.system.reboot(args.mode)
    print(f"Rebooting ({args.mode})")


async def cmd_update(flipper: Flipper, args) -> None:
    await flipper.system.update(args.manifest)
    await flipper.system.reboot(RebootMode.UPDATE)
    print("Update staged; the Flipper is rebooting to install it.")


async def cmd_ls(flipper: Flipper, args) -> None:
    entries = await flipper.storage.list(args.path, include_md5=args.md5)
    for entry in sorted(entries, key=lambda e: (not e.is_dir, e.name.lower())):
        if entry.is_dir:
            print(f"{entry.name}/")
        else:
            print(f"{entry.name}  ({entry.size} B)" + (f"  {entry.md5}" if args.md5 else ""))


async def cmd_tree(flipper: Flipper, args) -> None:
    async for directory, _, files in flipper.storage.walk(args.path):
        depth = 0 if directory == args.path else posixpath.relpath(directory, args.path).count("/") + 1
        print("  " * depth + (posixpath.basename(directory) or directory) + "/")
        for file in files:
            print("  " * (depth + 1) + file.name)


async def cmd_stat(flipper: Flipper, args) -> None:
    entry = await flipper.storage.stat(args.path)
    if entry is None:
        sys.exit(f"error: {args.path} doesn't exist")
    print(f"{'directory' if entry.is_dir else 'file'}  {entry.size} B  modified {await flipper.storage.timestamp(args.path)}")


async def cmd_df(flipper: Flipper, args) -> None:
    total, free = await flipper.storage.space(args.path)
    print(f"{args.path}: {free / 1e6:.1f} MB free of {total / 1e6:.1f} MB")


async def cmd_md5(flipper: Flipper, args) -> None:
    print(await flipper.storage.md5(args.path))


async def cmd_cat(flipper: Flipper, args) -> None:
    sys.stdout.buffer.write(await flipper.storage.read(args.path))


def _progress(label: str):
    """A progress callback that redraws one stderr line, or None when stderr isn't a terminal."""
    def report(done: int, total: int) -> None:
        print(f"\r{label}: {done}/{total}", end="\n" if done == total else "", file=sys.stderr, flush=True)
    return report if sys.stderr.isatty() else None


async def cmd_get(flipper: Flipper, args) -> None:
    local = Path(args.local or posixpath.basename(args.remote.rstrip("/")))
    await flipper.storage.download(args.remote, local, _progress("Downloading"))
    print(f"Saved {args.remote} to {local}")


async def cmd_put(flipper: Flipper, args) -> None:
    await flipper.storage.upload(args.local, args.remote, _progress("Uploading"))
    print(f"Uploaded {args.local} to {args.remote}")


async def cmd_mkdir(flipper: Flipper, args) -> None:
    await flipper.storage.mkdir(args.path, exist_ok=True, parents=True)


async def cmd_rm(flipper: Flipper, args) -> None:
    await flipper.storage.delete(args.path, recursive=args.recursive)


async def cmd_mv(flipper: Flipper, args) -> None:
    await flipper.storage.rename(args.old, args.new)


async def cmd_backup(flipper: Flipper, args) -> None:
    await flipper.storage.backup(args.archive)
    print(f"Backed up internal storage to {args.archive}")


async def cmd_restore(flipper: Flipper, args) -> None:
    await flipper.storage.restore(args.archive)
    print(f"Restored internal storage from {args.archive}")


async def cmd_screenshot(flipper: Flipper, args) -> None:
    if args.live:
        async with flipper.gui.stream() as frames:
            async for screen in frames:
                print("\x1b[H\x1b[2J" + screen.to_text(), flush=True)
        return
    screen = await flipper.gui.screenshot()
    if args.output:
        Path(args.output).write_bytes(screen.to_png(scale=args.scale))
        print(f"Saved {args.output}")
    else:
        print(screen.to_text())


async def cmd_press(flipper: Flipper, args) -> None:
    for key in args.keys:
        if args.hold:
            await flipper.gui.hold(key, args.hold)
        else:
            await flipper.gui.press(key, long=args.long)


async def cmd_lock(flipper: Flipper, args) -> None:
    if args.unlock:
        await flipper.desktop.unlock()
    print("locked" if await flipper.desktop.is_locked() else "unlocked")


async def cmd_gpio(flipper: Flipper, args) -> None:
    gpio = flipper.gpio
    if args.action == "otg":
        if args.value is not None:
            await gpio.set_otg(args.value == "on")
        print("5V on" if await gpio.otg() else "5V off")
    elif args.action == "read":
        await gpio.set_mode(args.pin, "input")
        if args.pull:
            await gpio.set_pull(args.pin, args.pull)
        print(int(await gpio.read(args.pin)))
    elif args.action == "write":
        await gpio.set_mode(args.pin, "output")
        await gpio.write(args.pin, args.value == "1")
    elif args.action == "mode":
        mode = await gpio.mode(args.pin)
        print(mode.name.lower() if mode else "unconfigured")


async def cmd_app(flipper: Flipper, args) -> None:
    if args.action == "start":
        await flipper.apps.start(args.name, args.args)
    elif args.action == "exit":
        await flipper.apps.exit()
    else:
        print("an app is open" if await flipper.apps.is_locked() else "no app is open")


async def cmd_subghz_tx(flipper: Flipper, args) -> None:
    await flipper.subghz.transmit_file(args.path, seconds=args.seconds)
    print(f"Transmitted {args.path}")


async def cmd_ir_tx(flipper: Flipper, args) -> None:
    button = int(args.button) if args.button.isdigit() else args.button
    await flipper.infrared.send(args.path, button, seconds=args.hold)
    print(f"Sent {args.button}")


async def cmd_ir_list(flipper: Flipper, args) -> None:
    for i, signal in enumerate(await flipper.infrared.signals(args.path)):
        print(f"{i:>3}  {signal.name}  ({signal.protocol or signal.type})")


async def cmd_emulate(flipper: Flipper, args) -> None:
    emulator = getattr(flipper, args.kind)
    async with emulator.emulate(args.path):
        if args.seconds:
            print(f"Emulating {args.path} for {args.seconds:g}s")
            await asyncio.sleep(args.seconds)
        else:
            print(f"Emulating {args.path}; press Ctrl-C to stop")
            await flipper.wait_disconnected()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flipperzero-ble", description="Control a Flipper Zero over Bluetooth LE.")
    parser.add_argument(
        "-d", "--device", default=os.environ.get("FLIPPER_DEVICE"),
        help="Flipper name (e.g. R3loma) or BLE address; defaults to $FLIPPER_DEVICE or the nearest Flipper",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name, handler, help_text):
        sub = commands.add_parser(name, help=help_text)
        sub.set_defaults(handler=handler)
        return sub

    pins = [p.name.lower() for p in Pin] + [str(p.header_number) for p in Pin]

    add("scan", cmd_scan, "list nearby Flippers").add_argument("--timeout", type=float, default=10.0)
    add("info", cmd_info, "firmware, hardware and battery").add_argument("--all", action="store_true")
    add("props", cmd_props, "read a property group (devinfo, pwrinfo, pwrdebug)").add_argument(
        "key", nargs="?", default="devinfo")
    add("ping", cmd_ping, "check the connection")
    add("alert", cmd_alert, "beep and flash the Flipper")
    add("time", cmd_time, "show the Flipper's clock").add_argument(
        "--sync", action="store_true", help="set it to this computer's time first")
    add("reboot", cmd_reboot, "restart the Flipper").add_argument(
        "mode", nargs="?", default="os", choices=[m.name.lower() for m in RebootMode])
    add("update", cmd_update, "install an update package already on the SD card").add_argument(
        "manifest", help="e.g. /ext/update/f7-update-1.4.3/update.fuf")

    ls = add("ls", cmd_ls, "list a directory")
    ls.add_argument("path", nargs="?", default="/ext")
    ls.add_argument("--md5", action="store_true", help="show each file's MD5")
    add("tree", cmd_tree, "list a directory recursively").add_argument("path", nargs="?", default="/ext")
    add("stat", cmd_stat, "size and modification time").add_argument("path")
    add("df", cmd_df, "free space").add_argument("path", nargs="?", default="/ext")
    add("md5", cmd_md5, "MD5 of a file").add_argument("path")
    add("cat", cmd_cat, "print a file").add_argument("path")
    get = add("get", cmd_get, "download a file or directory")
    get.add_argument("remote")
    get.add_argument("local", nargs="?")
    put = add("put", cmd_put, "upload a file or directory")
    put.add_argument("local")
    put.add_argument("remote")
    add("mkdir", cmd_mkdir, "create a directory (and its parents)").add_argument("path")
    rm = add("rm", cmd_rm, "delete a file or directory")
    rm.add_argument("path")
    rm.add_argument("-r", "--recursive", action="store_true")
    mv = add("mv", cmd_mv, "move or rename")
    mv.add_argument("old")
    mv.add_argument("new")
    add("backup", cmd_backup, "archive internal storage to a .tar").add_argument(
        "archive", nargs="?", default="/ext/backup.tar")
    add("restore", cmd_restore, "restore internal storage from a .tar").add_argument(
        "archive", nargs="?", default="/ext/backup.tar")

    shot = add("screenshot", cmd_screenshot, "capture the screen (PNG if a path is given, else text)")
    shot.add_argument("output", nargs="?")
    shot.add_argument("--scale", type=int, default=4)
    shot.add_argument("--live", action="store_true", help="keep showing the screen in the terminal")
    press = add("press", cmd_press, "press buttons in order")
    press.add_argument("keys", nargs="+", choices=[k.name.lower() for k in Key], type=str.lower)
    press.add_argument("--long", action="store_true")
    press.add_argument("--hold", type=float, metavar="SECONDS", help="hold each button this long")
    add("lock", cmd_lock, "show whether the Flipper is locked").add_argument(
        "--unlock", action="store_true", help="unlock it first")

    gpio = add("gpio", cmd_gpio, "read and drive GPIO pins")
    gpio_actions = gpio.add_subparsers(dest="action", required=True)
    read = gpio_actions.add_parser("read", help="set a pin to input and read it")
    read.add_argument("pin", choices=pins, type=str.lower)
    read.add_argument("--pull", choices=["up", "down", "none"])
    write = gpio_actions.add_parser("write", help="set a pin to output and drive it")
    write.add_argument("pin", choices=pins, type=str.lower)
    write.add_argument("value", choices=["0", "1"])
    gpio_actions.add_parser("mode", help="show a pin's mode").add_argument("pin", choices=pins, type=str.lower)
    gpio_actions.add_parser("otg", help="show or switch the 5V supply on pin 1").add_argument(
        "value", nargs="?", choices=["on", "off"])

    app = add("app", cmd_app, "start or exit apps")
    app_actions = app.add_subparsers(dest="action", required=True)
    start = app_actions.add_parser("start", help="start an app by name (from the home screen)")
    start.add_argument("name")
    start.add_argument("args", nargs="?", default="")
    app_actions.add_parser("exit", help="exit the app running in RPC mode")
    app_actions.add_parser("status", help="show whether an app is open")

    tx = add("subghz-tx", cmd_subghz_tx, "transmit a .sub file stored on the Flipper")
    tx.add_argument("path")
    tx.add_argument("--seconds", type=float, default=0.5)
    ir = add("ir-tx", cmd_ir_tx, "send a button from an .ir remote stored on the Flipper")
    ir.add_argument("path")
    ir.add_argument("button", help="button name, or index from ir-list")
    ir.add_argument("--hold", type=float, metavar="SECONDS", help="hold the button (repeating) this long")
    add("ir-list", cmd_ir_list, "list the buttons in an .ir remote").add_argument("path")
    emulate = add("emulate", cmd_emulate, "emulate a saved NFC tag, RFID card or iButton key")
    emulate.add_argument("kind", choices=EMULATORS)
    emulate.add_argument("path")
    emulate.add_argument("--seconds", type=float, help="stop after this long (default: until Ctrl-C)")
    return parser


async def run(args) -> None:
    if args.handler is cmd_scan:
        await cmd_scan(args)
        return
    async with connect(args.device) as flipper:
        await args.handler(flipper, args)


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(run(args))
    except (FlipperError, FlipperNotFoundError, FlipperDisconnectedError) as exc:
        sys.exit(f"error: {exc}")
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
