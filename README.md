# flipperzero-ble

Control a [Flipper Zero](https://flipper.net) over Bluetooth LE from Python. It uses the same protobuf RPC protocol as the official mobile app, so no cable, custom firmware or on-device app is needed.

- Async API built on [bleak](https://github.com/hbldh/bleak) covering every command in the RPC protocol: storage, system info and settings, apps, screen streaming and a virtual display, the Flipper's buttons, GPIO, the lock screen, and remote control of Sub-GHz, Infrared, NFC, 125 kHz RFID and iButton.
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
| `flipper.storage` | `list` (optionally with MD5s), `walk`, `read`/`write` (with progress), `read_text`/`write_text`, `upload`/`download` (files or whole directories), `stat`, `exists`, `is_dir`, `timestamp`, `mkdir` (with `parents`), `delete`, `rename`, `md5`, `space`, `extract` (.tar), `backup`/`restore` (internal storage) |
| `flipper.system` | `ping`, `device_info`, `power_info`, `properties`, `protobuf_version`, `datetime`, `set_datetime`, `alert`, `reboot`, `update`, `factory_reset` |
| `flipper.apps` | `start`, `exit`, `is_locked`, `load_file`, `press`, `release`, `press_release`, `send_data`/`data()`, `states()`, `last_error`, `session(name)` |
| `flipper.gui` | `press(key, long=False)`, `hold(key, seconds)`, `send_input`, `screenshot()`, `stream()`, `virtual_display()` |
| `flipper.gpio` | `set_mode`, `mode`, `set_pull`, `read`, `write`, `otg`/`set_otg` (5V on pin 1) |
| `flipper.desktop` | `is_locked`, `unlock`, `lock_changes()` |
| `flipper.subghz` | `transmit_file`, `transmit`, `session()`, `info`; module functions `key_file()` and `parse()` |
| `flipper.infrared` | `send(path, button)`, `send_signal`, `signals`, `session()`; module functions `parsed_signal()`, `raw_signal()`, `remote_file()`, `parse()` |
| `flipper.nfc`, `flipper.rfid`, `flipper.ibutton` | `emulate(path)`, `emulate_for(path, seconds)`, `saved()` |

Failed commands raise `FlipperError`, with `.status` set to the protocol's status name (for example `ERROR_STORAGE_NOT_EXIST`). When an app in RPC mode rejects a command, you get `AppError`, a subclass carrying the app's own explanation in `.code` and `.text` (for example "Transmission on this frequency is restricted in your region"). If the Bluetooth link drops, waiting requests and subscriptions raise `FlipperDisconnectedError`; `flipper.connected` and `await flipper.wait_disconnected()` tell you when that happens.

### Any other command

Pass the `PB.Main` field name from [`flipper.proto`](https://github.com/flipperdevices/flipperzero-protobuf/blob/dev/flipper.proto) and its message as a dict:

```python
parts = await flipper.request(storage_timestamp_request={"path": "/ext/subghz"})
print(parts[0].storage_timestamp_response.timestamp)
```

`request()` returns every response part, because long replies are split across several messages; `iter_request()` yields them as they arrive. For commands whose input is split into parts, use `request_stream()`, and for messages that get no reply, `send()`.

Messages the Flipper sends unprompted (screen frames, app state changes, lock-screen changes) can be collected with `flipper.subscribe("desktop_status")`, which is an async iterator, or `flipper.stream(kind, start, stop)`, which also sends the commands that start and stop them. `flipper.add_listener()` takes a plain callback instead.

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

### Infrared

```python
from flipperzero_ble.infrared import parsed_signal

async with flipperzero_ble.connect() as flipper:
    print([s.name for s in await flipper.infrared.signals("/ext/infrared/TV.ir")])
    await flipper.infrared.send("/ext/infrared/TV.ir", "Power")

    async with flipper.infrared.session() as ir:
        await ir.load("/ext/infrared/TV.ir")
        await ir.send("Vol_up", seconds=1.5)  # hold, as if the button were held down
        await ir.send(0)                      # by index

    # A code that isn't saved anywhere: NEC address 0x07, command 0x02.
    await flipper.infrared.send_signal(parsed_signal("Power", "NEC", 0x07, 0x02))
```

### NFC, 125 kHz RFID and iButton emulation

```python
async with flipper.nfc.emulate("/ext/nfc/Office.nfc"):
    await asyncio.sleep(10)   # the Flipper emulates the tag until the block ends

await flipper.rfid.emulate_for("/ext/lfrfid/Gate.rfid", seconds=5)
print(await flipper.ibutton.saved())
```

Other apps with an RPC mode can be driven with `flipper.apps.session(name)`, which starts the app, waits until it reports it's ready, and exits it afterwards.

### Screen and buttons

```python
from flipperzero_ble import Screen

async with flipperzero_ble.connect() as flipper:
    async with flipper.gui.stream() as frames:     # a frame whenever the screen changes
        async for screen in frames:
            print(screen.to_text())
            break

    # Draw on the Flipper's screen yourself, and receive its buttons.
    async with flipper.gui.virtual_display(forward_input=True) as display:
        await display.draw(Screen.from_art("#" * 128))
        async for event in display.inputs():
            print(event.key, event.type)
            if event.key.name == "BACK":
                break

    await flipper.gui.hold("down", seconds=2)  # scroll fast through a menu
```

`Screen` converts between the screen stream's page layout (`from_frame`/`to_frame`), XBM (`from_xbm`/`to_xbm`), text art, Pillow images (`from_image`), terminal text and PNG.

### GPIO

```python
await flipper.gpio.set_mode("PA7", "output")   # or header pin 2, or Pin.PA7
await flipper.gpio.write("PA7", True)
await flipper.gpio.set_mode("PC0", "input")
await flipper.gpio.set_pull("PC0", "up")
print(await flipper.gpio.read("PC0"))
await flipper.gpio.set_otg(True)               # 5V on header pin 1
```

GPIO pins are 3.3V. The GPIO calls work from any screen; they don't need the GPIO app.

### System

```python
await flipper.system.set_datetime()                    # sync the clock to this computer
print(await flipper.system.properties("pwrinfo"))      # "devinfo", "pwrinfo" or "pwrdebug"
await flipper.storage.backup("/ext/int_backup.tar")    # settings, pairings, ...

# Firmware update from an unpacked update package (the folder inside an official .tgz).
await flipper.storage.upload("f7-update-1.4.3", "/ext/update/f7-update-1.4.3")
await flipper.system.update("/ext/update/f7-update-1.4.3/update.fuf")
await flipper.system.reboot("update")
```

`reboot()` and `factory_reset()` end the connection. `factory_reset()` erases internal storage, so back it up first.

## Command line

```text
flipperzero-ble [-d NAME_OR_ADDRESS] <command>

  scan                       list nearby Flippers
  info [--all]               firmware, hardware and battery
  props [devinfo|pwrinfo|pwrdebug]
  ping | alert               check the connection | beep and flash the Flipper
  time [--sync]              show the clock (after setting it to this computer's time)
  reboot [os|dfu|update]     restart the Flipper
  update MANIFEST            install an update package already on the SD card
  ls [PATH] [--md5] | tree [PATH] | stat PATH | df [PATH] | md5 PATH | cat PATH
  get REMOTE [LOCAL]         download a file or directory
  put LOCAL REMOTE           upload a file or directory
  mkdir PATH | rm [-r] PATH | mv OLD NEW
  backup [ARCHIVE] | restore [ARCHIVE]   internal storage to/from a .tar on the SD card
  screenshot [OUT.png] [--live]          capture the screen (text in the terminal if no path)
  press KEY... [--long | --hold SECONDS] press up/down/left/right/ok/back
  lock [--unlock]            show (or clear) the lock screen
  gpio read PIN [--pull up|down|none] | gpio write PIN 0|1 | gpio mode PIN | gpio otg [on|off]
  app start NAME [ARGS] | app exit | app status
  subghz-tx PATH             transmit a .sub file stored on the Flipper
  ir-list PATH               list the buttons in an .ir remote
  ir-tx PATH BUTTON [--hold SECONDS]     send a button by name or index
  emulate nfc|rfid|ibutton PATH [--seconds N]
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
