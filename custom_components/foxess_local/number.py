"""Number platform: standard (Self-Use) max SoC."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, SIGNAL_UPDATE


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    server = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([StandardMaxSocNumber(server)])


class StandardMaxSocNumber(NumberEntity, RestoreEntity):
    """The max SoC the battery charges to outside force-charge windows
    (the all-day Self-Use schedule group's maxSoc)."""

    _attr_has_entity_name = True
    _attr_name = "Standard Max SoC"
    _attr_unique_id = f"{DOMAIN}_standard_max_soc"
    _attr_native_min_value = 10
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:battery-charging-80"
    _attr_device_info = {
        "identifiers": {(DOMAIN, "kh")},
        "name": "FoxESS KH (local)",
        "manufacturer": "FoxESS",
        "model": "KH9.9",
    }

    def __init__(self, server) -> None:
        self._server = server
        self._attr_native_value = None

    def _from_store(self, store: dict):
        for g in store.get("schedule_groups") or []:
            if g.get("mode") == "SelfUse":
                return g.get("max_soc")
        return None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Prefer the live schedule value; fall back to the last restored value.
        store = self.hass.data.get(DOMAIN, {}).get("values", {})
        val = self._from_store(store)
        if val is None:
            last = await self.async_get_last_state()
            if last and last.state not in (None, "unknown", "unavailable"):
                try:
                    val = float(last.state)
                except ValueError:
                    val = None
        self._attr_native_value = val if val is not None else 100
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._handle_update)
        )

    @callback
    def _handle_update(self, store: dict) -> None:
        # Keep in sync if the schedule's Self-Use cap changes elsewhere.
        val = self._from_store(store)
        if val is not None and val != self._attr_native_value:
            self._attr_native_value = val
            self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        ok = await self._server.set_self_use_max_soc(int(value))
        if ok:
            self._attr_native_value = value
            self.async_write_ha_state()
