import argparse
import asyncio
import os
import sys
from pathlib import Path

from .client import Flipper, connect
from .discovery import scan
from .errors import FlipperError, FlipperNotFoundError
from .gui import Key


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


async def cmd_ping(flipper: Flipper, args) -> None:
    print(await flipper.system.ping(b"ping"))


async def cmd_alert(flipper: Flipper, args) -> None:
    await flipper.system.alert()


async def cmd_ls(flipper: Flipper, args) -> None:
    for entry in sorted(await flipper.storage.list(args.path), key=lambda e: (not e.is_dir, e.name.lower())):
        print(f"{entry.name}/" if entry.is_dir else f"{entry.name}  ({entry.size} B)")


async def cmd_cat(flipper: Flipper, args) -> None:
    sys.stdout.buffer.write(await flipper.storage.read(args.path))


async def cmd_get(flipper: Flipper, args) -> None:
    local = Path(args.local or Path(args.remote).name)
    local.write_bytes(await flipper.storage.read(args.remote))
    print(f"Saved {args.remote} to {local}")


async def cmd_put(flipper: Flipper, args) -> None:
    await flipper.storage.write(args.remote, Path(args.local).read_bytes())
    print(f"Uploaded {args.local} to {args.remote}")


async def cmd_mkdir(flipper: Flipper, args) -> None:
    await flipper.storage.mkdir(args.path, exist_ok=True)


async def cmd_rm(flipper: Flipper, args) -> None:
    await flipper.storage.delete(args.path, recursive=args.recursive)


async def cmd_screenshot(flipper: Flipper, args) -> None:
    screen = await flipper.gui.screenshot()
    if args.output:
        Path(args.output).write_bytes(screen.to_png(scale=args.scale))
        print(f"Saved {args.output}")
    else:
        print(screen.to_text())


async def cmd_press(flipper: Flipper, args) -> None:
    for key in args.keys:
        await flipper.gui.press(key, long=args.long)


async def cmd_subghz_tx(flipper: Flipper, args) -> None:
    await flipper.subghz.transmit_file(args.path, seconds=args.seconds)
    print(f"Transmitted {args.path}")


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

    add("scan", cmd_scan, "list nearby Flippers").add_argument("--timeout", type=float, default=10.0)
    add("info", cmd_info, "firmware, hardware and battery").add_argument("--all", action="store_true")
    add("ping", cmd_ping, "check the connection")
    add("alert", cmd_alert, "beep and flash the Flipper")
    add("ls", cmd_ls, "list a directory").add_argument("path", nargs="?", default="/ext")
    add("cat", cmd_cat, "print a file").add_argument("path")
    get = add("get", cmd_get, "download a file")
    get.add_argument("remote")
    get.add_argument("local", nargs="?")
    put = add("put", cmd_put, "upload a file")
    put.add_argument("local")
    put.add_argument("remote")
    add("mkdir", cmd_mkdir, "create a directory").add_argument("path")
    rm = add("rm", cmd_rm, "delete a file or directory")
    rm.add_argument("path")
    rm.add_argument("-r", "--recursive", action="store_true")
    shot = add("screenshot", cmd_screenshot, "capture the screen (PNG if a path is given, else text)")
    shot.add_argument("output", nargs="?")
    shot.add_argument("--scale", type=int, default=4)
    press = add("press", cmd_press, "press buttons in order")
    press.add_argument("keys", nargs="+", choices=[k.name.lower() for k in Key], type=str.lower)
    press.add_argument("--long", action="store_true")
    tx = add("subghz-tx", cmd_subghz_tx, "transmit a .sub file stored on the Flipper")
    tx.add_argument("path")
    tx.add_argument("--seconds", type=float, default=0.5)
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
    except (FlipperError, FlipperNotFoundError) as exc:
        sys.exit(f"error: {exc}")
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
