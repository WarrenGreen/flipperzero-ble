# flipperzero-ble

Control a [Flipper Zero](https://flipper.net) over Bluetooth LE from Python. It uses the same protobuf RPC protocol as the official mobile app, so no cable, custom firmware or on-device app is needed.

- Async API built on [bleak](https://github.com/hbldh/bleak): storage, system info, launching apps, screen capture, pressing the Flipper's buttons, Sub-GHz transmit.
- `request()` sends any command in Flipper's [protobuf definitions](https://github.com/flipperdevices/flipperzero-protobuf), including ones without a helper yet.
- A `flipperzero-ble` command-line tool.

Tested on macOS with official firmware 1.4.3. bleak also supports Linux and Windows, but they haven't been tested.

## Install

```bash
git clone --recurse-submodules https://github.com/WarrenGreen/flipperzero-ble.git
cd flipperzero-ble
pip install .
```

The protobuf definitions are a git submodule (`proto/`) and are compiled when the package is built, so clone with `--recurse-submodules`. If you already cloned without it, run `git submodule update --init`. `pip install git+https://github.com/WarrenGreen/flipperzero-ble.git` also works, because pip fetches submodules itself.

## Before you connect

1. On the Flipper, go to **Settings → Bluetooth** and turn Bluetooth on.
2. Disconnect the Flipper mobile app if it's connected. The Flipper accepts one connection at a time.
3. **The first time you connect, pair.** The Flipper shows a 6-digit PIN, and your OS asks for it (on macOS, a **Bluetooth Pairing Request** dialog). Enter it there; the SDK waits up to 2 minutes. After pairing, later connections don't need a PIN.

```bash
flipperzero-ble scan     # find nearby Flippers
flipperzero-ble info     # connect (pairing the first time), show firmware and battery
```

## Python API

```python
import asyncio
import flipperzero_ble


async def main():
    async with flipperzero_ble.connect() as flipper:  # nearest Flipper; or connect("R3loma") / connect(address)
        print(await flipper.system.device_info())
        print(await flipper.storage.list("/ext/subghz"))

        screen = await flipper.gui.screenshot()
        print(screen.to_text())

        await flipper.gui.press("back")


asyncio.run(main())
```

| Helper | Methods |
|---|---|
| `flipper.storage` | `list`, `read`, `write` (chunked), `stat`, `exists`, `mkdir`, `delete`, `rename`, `md5`, `space` |
| `flipper.system` | `ping`, `device_info`, `power_info`, `protobuf_version`, `datetime`, `alert` |
| `flipper.apps` | `start`, `exit`, `is_locked`, `load_file`, `press`, `release` |
| `flipper.gui` | `press(key, long=False)`, `screenshot()` returning a `Screen` with `.pixels`, `.to_text()`, `.to_png()` |
| `flipper.subghz` | `transmit_file`, `transmit`, `session()`; module functions `key_file()` and `parse()` |

Failed commands raise `FlipperError`, with `.status` set to the protocol's status name (for example `ERROR_STORAGE_NOT_EXIST`).

### Any other command

Pass the `PB.Main` field name from [`flipper.proto`](https://github.com/flipperdevices/flipperzero-protobuf/blob/dev/flipper.proto) and its message as a dict:

```python
parts = await flipper.request(storage_timestamp_request={"path": "/ext/subghz"})
print(parts[0].storage_timestamp_response.timestamp)
```

`request()` returns every response part, because long replies are split across several messages. For commands whose input is split into parts, use `request_stream()`. Messages the Flipper sends unprompted, such as screen frames, go to callbacks registered with `flipper.add_listener()`.

### Sub-GHz

```python
from flipperzero_ble.subghz import key_file

async with flipperzero_ble.connect() as flipper:
    await flipper.subghz.transmit_file("/ext/subghz/Garage.sub")

    # Several transmissions from one app launch; send() uploads the contents to a scratch file first.
    async with flipper.subghz.session() as tx:
        await tx.send(key_file("Princeton", 0x060716, 24, TE=399, Guard_time=30))
        await tx.send_file("/ext/subghz/Lamp_off.sub", seconds=1.0)
```

Apps can only be started from the Flipper's home screen. If another app is open, you'll get `ERROR_APP_SYSTEM_LOCKED`. Only transmit on frequencies you're allowed to use, and only to devices you own.

## Command line

```text
flipperzero-ble [-d NAME_OR_ADDRESS] <command>

  scan                     list nearby Flippers
  info [--all]             firmware, hardware and battery
  ping | alert             check the connection | beep and flash the Flipper
  ls [PATH]                list a directory (default /ext)
  cat PATH                 print a file
  get REMOTE [LOCAL]       download a file
  put LOCAL REMOTE         upload a file
  mkdir PATH | rm [-r] PATH
  screenshot [OUT.png]     capture the screen (text in the terminal if no path)
  press KEY... [--long]    press up/down/left/right/ok/back
  subghz-tx PATH           transmit a .sub file stored on the Flipper
```

Set `FLIPPER_DEVICE` to avoid passing `-d` every time. On macOS the address is a per-Mac UUID rather than the Flipper's real Bluetooth address, so using the Flipper's name is simpler.

## Development

```bash
git clone --recurse-submodules https://github.com/WarrenGreen/flipperzero-ble.git
cd flipperzero-ble
python -m venv .venv && .venv/bin/pip install -e ".[test]"
.venv/bin/pytest
```

The tests use a fake transport, so no Flipper is needed. To move to a newer protocol version, check out a different commit in `proto/` and reinstall.

How the connection works: the Flipper's Bluetooth serial service (`8fe5b3d5-…-7acc60fe0000`) has one characteristic you write to and one that sends replies as indications. A third, flow-control characteristic reports how many bytes the Flipper can accept; the transport never sends more than that. Each message is a length-prefixed `PB.Main` protobuf.

## License

MIT for this project. The protobuf definitions come from [flipperdevices/flipperzero-protobuf](https://github.com/flipperdevices/flipperzero-protobuf). They're linked as a submodule and not redistributed here. This project isn't affiliated with Flipper Devices.
