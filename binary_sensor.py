from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .entity import HondaLinkEntity
from .util import any_light_on, any_open_state, ev_flag, ev_info, ev_value, get_path, hhmm, status_body


@dataclass(frozen=True, kw_only=True)
class HondaLinkBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], bool | None]
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    # Created only for vehicles on the EV (Clarity) dashboard, if exists_fn holds.
    ev_only: bool = False
    # EV-dashboard vehicles: create the entity only if this holds for the first
    # snapshot. Without it, non-EV sensors are not created for those vehicles.
    exists_fn: Callable[[dict[str, Any]], bool] | None = None


def _ev_has(key: str) -> Callable[[dict[str, Any]], bool]:
    return lambda body: key in ev_info(body)


def _plugged_in(body: dict[str, Any]) -> bool | None:
    value = ev_value(body, "plugStatus")
    if value is None:
        return None
    return str(value).strip() != "0"


def _charge_attributes(body: dict[str, Any]) -> dict[str, Any]:
    # Code meanings beyond "1 = charging" are not documented; expose them raw.
    return {
        "charge_status_code": ev_value(body, "chargeStatus"),
        "charge_mode_code": ev_value(body, "chargeMode1") or ev_value(body, "chargeMode"),
        "charge_timer_setting_code": ev_value(body, "currentTimerSetting"),
        "scheduled_charge_start": hhmm(ev_value(body, "chargeStartTime1")),
        "scheduled_charge_end": hhmm(ev_value(body, "chargeEndTime1")),
    }


DOOR_KEYS = ["firstRowDriver", "firstRowPassenger", "secondRowDriver", "secondRowPassenger"]
WINDOW_KEYS = ["frontWindowDR", "frontWindowAS", "rearWindowRR", "rearWindowRL"]


BINARY_SENSORS: tuple[HondaLinkBinarySensorDescription, ...] = (
    HondaLinkBinarySensorDescription(
        key="any_door_open",
        translation_key="any_door_open",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda body: any_open_state(body, "doorStatus", DOOR_KEYS),
    ),
    HondaLinkBinarySensorDescription(
        key="hood_open",
        translation_key="hood_open",
        device_class=BinarySensorDeviceClass.OPENING,
        value_fn=lambda body: any_open_state(body, "doorStatus", ["hood"]),
    ),
    HondaLinkBinarySensorDescription(
        key="trunk_open",
        translation_key="trunk_open",
        device_class=BinarySensorDeviceClass.OPENING,
        value_fn=lambda body: any_open_state(body, "doorStatus", ["trunk"]),
    ),
    HondaLinkBinarySensorDescription(
        key="any_window_open",
        translation_key="any_window_open",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda body: any_open_state(body, "windowStatus", WINDOW_KEYS, "closeState"),
    ),
    HondaLinkBinarySensorDescription(
        key="any_light_on",
        translation_key="any_light_on",
        device_class=BinarySensorDeviceClass.LIGHT,
        value_fn=any_light_on,
    ),
    HondaLinkBinarySensorDescription(
        key="warning_lamp_on",
        translation_key="warning_lamp_on",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda body: any(
            str(message.get("condition", "")).upper() == "ON"
            for group in get_path(body, "warningLamps.data", []) or []
            if isinstance(group, dict)
            for message in group.get("messages", [])
            if isinstance(message, dict)
        ),
    ),
    HondaLinkBinarySensorDescription(
        key="remote_engine_running",
        translation_key="remote_engine_running",
        value_fn=lambda body: str(get_path(body, "remoteEngineStart.vehicleStartEvent.resStatus", "")).upper() == "ON",
    ),
    # EV (Clarity) dashboard. Codes per the HondaLink app: chargeStatus "1" =
    # charging, plugStatus "0" = unplugged, acStatus "1" = climate on,
    # igStatus "1" = ignition on.
    HondaLinkBinarySensorDescription(
        key="plugged_in",
        translation_key="plugged_in",
        device_class=BinarySensorDeviceClass.PLUG,
        ev_only=True,
        exists_fn=_ev_has("plugStatus"),
        value_fn=lambda body: _plugged_in(body),
    ),
    HondaLinkBinarySensorDescription(
        key="charging",
        translation_key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        ev_only=True,
        exists_fn=_ev_has("chargeStatus"),
        value_fn=lambda body: ev_flag(body, "chargeStatus"),
        attr_fn=_charge_attributes,
    ),
    HondaLinkBinarySensorDescription(
        key="climate_on",
        translation_key="climate_on",
        device_class=BinarySensorDeviceClass.RUNNING,
        ev_only=True,
        exists_fn=_ev_has("acStatus"),
        value_fn=lambda body: ev_flag(body, "acStatus"),
    ),
    HondaLinkBinarySensorDescription(
        key="ignition_on",
        translation_key="ignition_on",
        device_class=BinarySensorDeviceClass.POWER,
        ev_only=True,
        exists_fn=_ev_has("igStatus"),
        value_fn=lambda body: ev_flag(body, "igStatus"),
    ),
)


def _should_create(description: HondaLinkBinarySensorDescription, ev_dashboard: bool, body: dict[str, Any]) -> bool:
    if not ev_dashboard:
        return not description.ev_only
    return description.exists_fn is not None and description.exists_fn(body)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    body = status_body(coordinator.data or {})
    async_add_entities(
        HondaLinkBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSORS
        if _should_create(description, coordinator.ev_dashboard, body)
    )


class HondaLinkBinarySensor(HondaLinkEntity, BinarySensorEntity):
    entity_description: HondaLinkBinarySensorDescription

    def __init__(self, coordinator, entry: ConfigEntry, description: HondaLinkBinarySensorDescription) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(status_body(self.coordinator.data or {}))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attr_fn is None:
            return None
        return self.entity_description.attr_fn(status_body(self.coordinator.data or {}))
