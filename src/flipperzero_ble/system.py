from __future__ import annotations

import datetime
from enum import Enum
from typing import TYPE_CHECKING

from ._proto import system_pb2
from .errors import FlipperError

if TYPE_CHECKING:
    from .client import Flipper


class RebootMode(Enum):
    OS = system_pb2.RebootRequest.OS
    DFU = system_pb2.RebootRequest.DFU
    UPDATE = system_pb2.RebootRequest.UPDATE


class System:
    def __init__(self, flipper: "Flipper"):
        self._flipper = flipper

    async def ping(self, data: bytes = b"") -> bytes:
        parts = await self._flipper.request(system_ping_request={"data": data})
        return parts[0].system_ping_response.data

    async def device_info(self) -> dict[str, str]:
        """Firmware, hardware and radio details, e.g. `firmware_version`, `hardware_name`."""
        parts = await self._flipper.request(system_device_info_request={})
        return {p.system_device_info_response.key: p.system_device_info_response.value for p in parts}

    async def power_info(self) -> dict[str, str]:
        """Battery and charging details, e.g. `charge_level`, `battery_voltage`."""
        parts = await self._flipper.request(system_power_info_request={})
        return {p.system_power_info_response.key: p.system_power_info_response.value for p in parts}

    async def properties(self, key: str) -> dict[str, str]:
        """Reads a property group: "devinfo", "pwrinfo" or "pwrdebug", optionally narrowed with a dotted
        prefix such as "devinfo.firmware". Keys come back dotted and without the group, e.g. "firmware.version".
        """
        parts = await self._flipper.request(property_get_request={"key": key})
        return {
            p.property_get_response.key: p.property_get_response.value
            for p in parts
            if p.WhichOneof("content") == "property_get_response"
        }

    async def protobuf_version(self) -> tuple[int, int]:
        parts = await self._flipper.request(system_protobuf_version_request={})
        version = parts[0].system_protobuf_version_response
        return version.major, version.minor

    async def alert(self) -> None:
        """Beeps, vibrates and flashes the LED, handy for finding which Flipper you're connected to."""
        await self._flipper.request(system_play_audiovisual_alert_request={})

    async def datetime(self) -> datetime.datetime:
        """The Flipper's clock. It has no time zone, so this is a naive datetime in whatever zone it was set to."""
        parts = await self._flipper.request(system_get_datetime_request={})
        dt = parts[0].system_get_datetime_response.datetime
        return datetime.datetime(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)

    async def set_datetime(self, when: datetime.datetime | None = None) -> None:
        """Sets the Flipper's clock, to this computer's local time if `when` is omitted."""
        when = when or datetime.datetime.now()
        await self._flipper.request(
            system_set_datetime_request={
                "datetime": {
                    "year": when.year,
                    "month": when.month,
                    "day": when.day,
                    "hour": when.hour,
                    "minute": when.minute,
                    "second": when.second,
                    "weekday": when.isoweekday(),
                }
            }
        )

    async def reboot(self, mode: RebootMode | str = RebootMode.OS) -> None:
        """Restarts the Flipper, which ends the connection. Modes: "os" (normal), "dfu" (firmware recovery),
        "update" (install the update prepared with update())."""
        mode = RebootMode[mode.upper()] if isinstance(mode, str) else mode
        # The Flipper reboots without replying.
        await self._flipper.send(system_reboot_request={"mode": mode.value})

    async def update(self, manifest_path: str) -> None:
        """Stages a firmware update from an unpacked update package on the SD card, e.g.
        "/ext/update/f7-update-1.4.3/update.fuf". Call reboot("update") afterwards to install it."""
        try:
            await self._flipper.request(system_update_request={"update_manifest": manifest_path})
        except FlipperError as exc:
            if exc.response is not None and exc.response.WhichOneof("content") == "system_update_response":
                code = system_pb2.UpdateResponse.UpdateResultCode.Name(exc.response.system_update_response.code)
                raise FlipperError(exc.status, f"Update rejected: {code}", exc.response) from exc
            raise

    async def factory_reset(self) -> None:
        """Erases internal storage (settings, pairings, saved keys on /int) and reboots. The SD card is kept."""
        await self._flipper.send(system_factory_reset_request={})
