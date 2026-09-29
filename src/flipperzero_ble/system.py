import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import Flipper


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

    async def protobuf_version(self) -> tuple[int, int]:
        parts = await self._flipper.request(system_protobuf_version_request={})
        version = parts[0].system_protobuf_version_response
        return version.major, version.minor

    async def alert(self) -> None:
        """Beeps, vibrates and flashes the LED, handy for finding which Flipper you're connected to."""
        await self._flipper.request(system_play_audiovisual_alert_request={})

    async def datetime(self) -> datetime.datetime:
        parts = await self._flipper.request(system_get_datetime_request={})
        dt = parts[0].system_get_datetime_response.datetime
        return datetime.datetime(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
