from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfLength, UnitOfPressure, UnitOfSpeed, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .entity import HondaLinkEntity
from .util import (
    ev_info,
    ev_total_range,
    ev_unit,
    ev_value,
    find_12v_battery_candidates,
    find_12v_battery_status,
    get_path,
    leaf_paths,
    parse_compact_utc,
    parse_iso_datetime,
    status_body,
    to_float,
    to_int,
)


@dataclass(frozen=True, kw_only=True)
class HondaLinkSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    # Unit reported in the response, when it can vary (EV dashboard: mile/km, F/C).
    unit_fn: Callable[[dict[str, Any]], str | None] | None = None
    # Created only for vehicles on the EV (Clarity) dashboard, if exists_fn holds.
    ev_only: bool = False
    # Never created for EV-dashboard vehicles.
    cig_only: bool = False
    # EV-dashboard vehicles: create the entity only if this holds for the first
    # snapshot. Defaults to "value_fn returns a value".
    exists_fn: Callable[[dict[str, Any]], bool] | None = None


_DISTANCE_UNITS = {"mile": UnitOfLength.MILES, "miles": UnitOfLength.MILES, "mi": UnitOfLength.MILES, "km": UnitOfLength.KILOMETERS}
_TEMPERATURE_UNITS = {"f": UnitOfTemperature.FAHRENHEIT, "c": UnitOfTemperature.CELSIUS}


def _ev_distance_unit(key: str) -> Callable[[dict[str, Any]], str | None]:
    return lambda body: _DISTANCE_UNITS.get(str(ev_unit(body, key) or "").lower())


def _ev_temperature_unit(key: str) -> Callable[[dict[str, Any]], str | None]:
    return lambda body: _TEMPERATURE_UNITS.get(str(ev_unit(body, key) or "").lower())


def _ev_has(key: str) -> Callable[[dict[str, Any]], bool]:
    return lambda body: key in ev_info(body)


def _tire(path: str):
    return lambda body: to_int(get_path(body, f"tireStatus.{path}.pressureData.value"))


SENSORS: tuple[HondaLinkSensorDescription, ...] = (
    HondaLinkSensorDescription(
        key="fuel_level",
        translation_key="fuel_level",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda body: to_int(get_path(body, "fuelLevel.currentLevel.value")),
    ),
    HondaLinkSensorDescription(
        key="range",
        translation_key="range",
        native_unit_of_measurement=UnitOfLength.MILES,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda body: to_int(get_path(body, "fuelLevel.driveRange.value")),
    ),
    HondaLinkSensorDescription(
        key="odometer",
        translation_key="odometer",
        native_unit_of_measurement=UnitOfLength.MILES,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda body: to_int(get_path(body, "odometer.value")),
    ),
    HondaLinkSensorDescription(
        key="oil_life",
        translation_key="oil_life",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda body: to_int(get_path(body, "oilLife.value")),
    ),
    HondaLinkSensorDescription(
        key="12v_battery_status",
        translation_key="12v_battery_status",
        cig_only=True,
        value_fn=find_12v_battery_status,
        attr_fn=lambda body: {
            "battery_candidates": find_12v_battery_candidates(body),
            "dashboard_keys": sorted(body.keys()),
            "dashboard_leaf_paths": leaf_paths(body),
        },
    ),
    HondaLinkSensorDescription(
        key="front_left_tire_pressure",
        translation_key="front_left_tire_pressure",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_tire("frontLeft"),
    ),
    HondaLinkSensorDescription(
        key="front_right_tire_pressure",
        translation_key="front_right_tire_pressure",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_tire("frontRight"),
    ),
    HondaLinkSensorDescription(
        key="rear_left_tire_pressure",
        translation_key="rear_left_tire_pressure",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_tire("rearLeft"),
    ),
    HondaLinkSensorDescription(
        key="rear_right_tire_pressure",
        translation_key="rear_right_tire_pressure",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_tire("rearRight"),
    ),
    HondaLinkSensorDescription(
        key="vehicle_speed",
        translation_key="vehicle_speed",
        native_unit_of_measurement=UnitOfSpeed.MILES_PER_HOUR,
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda body: to_float(get_path(body, "gpsData.velocity.value")),
    ),
    HondaLinkSensorDescription(
        key="remote_engine_status",
        translation_key="remote_engine_status",
        value_fn=lambda body: get_path(body, "remoteEngineStart.vehicleStartEvent.resStatus"),
    ),
    HondaLinkSensorDescription(
        key="last_update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda body: parse_compact_utc(ev_value(body, "updTime")) or parse_iso_datetime(get_path(body, "timestamp")),
        exists_fn=lambda body: ev_value(body, "updTime") is not None or get_path(body, "timestamp") is not None,
    ),
    HondaLinkSensorDescription(
        key="battery_level",
        translation_key="battery_level",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        ev_only=True,
        exists_fn=_ev_has("soc"),
        value_fn=lambda body: to_float(ev_value(body, "soc")),
    ),
    HondaLinkSensorDescription(
        key="ev_range",
        translation_key="ev_range",
        native_unit_of_measurement=UnitOfLength.MILES,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        ev_only=True,
        exists_fn=_ev_has("evRange"),
        unit_fn=_ev_distance_unit("evRange"),
        value_fn=lambda body: to_float(ev_value(body, "evRange")),
    ),
    HondaLinkSensorDescription(
        key="gas_range",
        translation_key="gas_range",
        native_unit_of_measurement=UnitOfLength.MILES,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        ev_only=True,
        exists_fn=_ev_has("petRange"),
        unit_fn=_ev_distance_unit("petRange"),
        value_fn=lambda body: to_float(ev_value(body, "petRange")),
    ),
    HondaLinkSensorDescription(
        key="total_range",
        translation_key="total_range",
        native_unit_of_measurement=UnitOfLength.MILES,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        ev_only=True,
        exists_fn=lambda body: "totalRange" in ev_info(body) or ("evRange" in ev_info(body) and "petRange" in ev_info(body)),
        unit_fn=lambda body: _DISTANCE_UNITS.get(
            str(ev_unit(body, "totalRange") or ev_unit(body, "evRange") or "").lower()
        ),
        value_fn=ev_total_range,
    ),
    HondaLinkSensorDescription(
        key="cabin_temperature",
        translation_key="cabin_temperature",
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        ev_only=True,
        exists_fn=_ev_has("intTemp"),
        unit_fn=_ev_temperature_unit("intTemp"),
        value_fn=lambda body: to_float(ev_value(body, "intTemp")),
    ),
    HondaLinkSensorDescription(
        key="outside_temperature",
        translation_key="outside_temperature",
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        ev_only=True,
        exists_fn=_ev_has("extTemp"),
        unit_fn=_ev_temperature_unit("extTemp"),
        value_fn=lambda body: to_float(ev_value(body, "extTemp")),
    ),
)


def _should_create(description: HondaLinkSensorDescription, ev_dashboard: bool, body: dict[str, Any]) -> bool:
    if not ev_dashboard:
        # Unchanged behaviour for CIG vehicles: every non-EV sensor.
        return not description.ev_only
    if description.cig_only:
        return False
    if description.exists_fn is not None:
        return description.exists_fn(body)
    return description.value_fn(body) is not None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    body = status_body(coordinator.data or {})
    async_add_entities(
        HondaLinkSensor(coordinator, entry, description)
        for description in SENSORS
        if _should_create(description, coordinator.ev_dashboard, body)
    )


class HondaLinkSensor(HondaLinkEntity, SensorEntity):
    entity_description: HondaLinkSensorDescription

    def __init__(self, coordinator, entry: ConfigEntry, description: HondaLinkSensorDescription) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(status_body(self.coordinator.data or {}))

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.unit_fn is not None:
            unit = self.entity_description.unit_fn(status_body(self.coordinator.data or {}))
            if unit:
                return unit
        return self.entity_description.native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attr_fn is None:
            return None
        return self.entity_description.attr_fn(status_body(self.coordinator.data or {}))
