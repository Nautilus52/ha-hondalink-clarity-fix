from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    HondaLinkAPI,
    HondaLinkCommandError,
    HondaLinkError,
    HondaLinkUnsupportedVehicleError,
    telematics_platform,
)
from .const import CONF_SCAN_INTERVAL, CONF_VEHICLE_INFO, CONF_VIN, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


def unsupported_vehicle_message(vehicle_info: dict[str, Any] | None) -> str:
    vehicle_info = vehicle_info or {}
    platform = telematics_platform(vehicle_info) or "not reported"
    name = " ".join(
        str(part)
        for part in (
            vehicle_info.get("ModelYear"),
            vehicle_info.get("ModelGroupNameFriendly") or vehicle_info.get("ModelCode") or "vehicle",
        )
        if part
    )
    return (
        f"Honda's dashboard API rejected both the standard and the EV (Clarity) "
        f"dashboard request for this {name} (TelematicsPlatform: {platform}, error "
        "0001-01-1151). This vehicle's telematics platform is not supported yet. "
        "See https://github.com/daviddelahoz/ha-hondalink/issues/4"
    )


class HondaLinkDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: HondaLinkAPI) -> None:
        self.api = api
        self.vin = entry.data[CONF_VIN]
        self.vehicle_info: dict[str, Any] = entry.data.get(CONF_VEHICLE_INFO) or {}
        interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            logging.getLogger(__name__),
            name=f"{DOMAIN}-{self.vin}",
            update_interval=timedelta(minutes=interval),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.api.async_get_dashboard_latest(self.vin)
        except HondaLinkUnsupportedVehicleError as err:
            # Permanent: stop Home Assistant from retrying setup forever.
            _LOGGER.debug("Dashboard request rejected as unsupported: %s", err)
            raise ConfigEntryError(unsupported_vehicle_message(self.vehicle_info)) from err
        except HondaLinkError as err:
            raise UpdateFailed(str(err)) from err

    @property
    def ev_dashboard(self) -> bool:
        """True for vehicles served by the EV (Clarity) dashboard request."""
        return self.api.ev_dashboard

    async def async_force_refresh(self) -> None:
        if self.ev_dashboard:
            # The app asks a Clarity for fresh data through the older GTC backend
            # (RegisterRemoteList), which is not implemented yet. Re-read instead.
            await self.async_refresh()
            return
        try:
            await self.api.async_request_dashboard_update(self.vin)
        except HondaLinkCommandError as err:
            _LOGGER.warning("HondaLink live dashboard refresh request failed: %s", err)
        except HondaLinkError as err:
            _LOGGER.warning("HondaLink live dashboard refresh request failed: %s", err)
        await self.async_refresh()

    async def async_start_engine(self) -> None:
        await self._run_command(self.api.async_start_engine)

    async def async_stop_engine(self) -> None:
        await self._run_command(self.api.async_stop_engine)

    async def async_lock(self) -> None:
        await self._run_command(self.api.async_lock)

    async def async_unlock(self) -> None:
        await self._run_command(self.api.async_unlock)

    async def async_horn(self) -> None:
        await self._run_command(self.api.async_horn)

    async def async_lights(self) -> None:
        await self._run_command(self.api.async_lights)

    async def async_stop_horn_lights(self) -> None:
        await self._run_command(self.api.async_stop_horn_lights)

    async def _run_command(self, command) -> None:
        try:
            await command()
        except HondaLinkCommandError as err:
            raise HomeAssistantError(str(err)) from err
        await self.async_request_refresh()
