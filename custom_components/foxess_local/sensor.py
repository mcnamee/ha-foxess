"""Sensor platform for FoxESS Local."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, RAW_REG_END, RAW_REG_START, SIGNAL_UPDATE
from .protocol import KNOWN_HOLDING

# Derived power sensors (computed in protocol.derive_virtual):
# key -> (name, unit, device_class)
DERIVED_SENSORS = {
    "grid_consumption_power": ("Grid Consumption Power", "kW", "power"),
    "feedin_power": ("Feed-in Power", "kW", "power"),
    "load_power": ("Load Power", "kW", "power"),
    "battery_charge_power": ("Battery Charge Power", "kW", "power"),
    "battery_discharge_power": ("Battery Discharge Power", "kW", "power"),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    entities: list[SensorEntity] = []
    # labelled register sensors
    for _reg, (key, name, unit, dclass, _scale) in KNOWN_HOLDING.items():
        entities.append(FoxessSensor(key, name, unit, dclass))
    # derived power sensors
    for key, (name, unit, dclass) in DERIVED_SENSORS.items():
        entities.append(FoxessSensor(key, name, unit, dclass))
    # raw register sensors for diagnostics
    for reg in range(RAW_REG_START, RAW_REG_END + 1):
        key = f"r{reg:04x}"
        entities.append(FoxessSensor(key, key, None, None, diagnostic=True))
    # current schedule (state = summary, attributes = parsed groups)
    entities.append(FoxessScheduleSensor())
    async_add_entities(entities)


class FoxessSensor(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, key, name, unit, device_class, diagnostic=False):
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        # daily energy counters accumulate then reset at midnight
        self._attr_state_class = (
            SensorStateClass.TOTAL_INCREASING if device_class == "energy"
            else SensorStateClass.MEASUREMENT
        )
        if diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_entity_registry_enabled_default = False
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "kh")},
            "name": "FoxESS KH (local)",
            "manufacturer": "FoxESS",
            "model": "KH9.9",
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._handle_update)
        )
        store = self.hass.data.get(DOMAIN, {}).get("values", {})
        if self._key in store:
            self._attr_native_value = store[self._key]

    @callback
    def _handle_update(self, store: dict) -> None:
        if self._key in store:
            self._attr_native_value = store[self._key]
            self.async_write_ha_state()


class FoxessScheduleSensor(SensorEntity):
    """Current inverter schedule: state = summary, attributes = parsed groups."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Schedule"
    _attr_icon = "mdi:calendar-clock"
    _attr_unique_id = f"{DOMAIN}_schedule"

    _attr_device_info = {
        "identifiers": {(DOMAIN, "kh")},
        "name": "FoxESS KH (local)",
        "manufacturer": "FoxESS",
        "model": "KH9.9",
    }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._handle_update)
        )
        self._handle_update(self.hass.data.get(DOMAIN, {}).get("values", {}))

    @callback
    def _handle_update(self, store: dict) -> None:
        if "schedule_summary" in store:
            self._attr_native_value = store["schedule_summary"][:255]
            self._attr_extra_state_attributes = {"groups": store.get("schedule_groups", [])}
            self.async_write_ha_state()
