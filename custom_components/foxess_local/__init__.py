"""The FoxESS Local integration."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DEFAULT_PORT, DOMAIN, SIGNAL_UPDATE
from .protocol import derive_virtual
from .schedule import build_force_charge
from .server import FoxessServer, ensure_cert

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up FoxESS Local from a config entry."""
    port = entry.data.get("port", DEFAULT_PORT)
    cert_path = hass.config.path(f"{DOMAIN}_cert.pem")
    key_path = hass.config.path(f"{DOMAIN}_key.pem")
    await hass.async_add_executor_job(ensure_cert, cert_path, key_path)

    data = hass.data.setdefault(DOMAIN, {})
    store = data.setdefault("values", {})

    def update_cb(values: dict) -> None:
        store.update(values)
        store.update(derive_virtual(store))  # grid_consumption_power, load_power, etc.
        async_dispatcher_send(hass, SIGNAL_UPDATE, store)

    server = FoxessServer(port, cert_path, key_path, update_cb)
    try:
        await server.start()
    except OSError as err:
        _LOGGER.error("Could not bind port %s: %s", port, err)
        raise
    data[entry.entry_id] = server

    async def handle_force_charge(call: ServiceCall) -> dict:
        writes = build_force_charge(
            call.data["start"], call.data["end"], call.data["max_soc"],
            call.data.get("fd_pwr", 10500), call.data.get("work_mode", "ForceCharge"),
        )
        result = await server.send_schedule(writes, dry_run=call.data.get("dry_run", True))
        # normalise to a dict so the action can return a response
        if isinstance(result, dict):
            return {"dry_run": False, **result}
        return {"dry_run": True, "would_write": len(writes), "frames": result}

    hass.services.async_register(
        DOMAIN, "force_charge", handle_force_charge,
        schema=vol.Schema({
            vol.Required("start"): cv.string,          # "HH:MM"
            vol.Required("end"): cv.string,            # "HH:MM"
            # Coerce so templated automations (which render to strings) work.
            vol.Required("max_soc"): vol.All(vol.Coerce(int), vol.Range(min=10, max=100)),
            vol.Optional("fd_pwr", default=10500): vol.All(vol.Coerce(int), vol.Range(min=0, max=12000)),
            vol.Optional("work_mode", default="ForceCharge"): cv.string,
            vol.Optional("dry_run", default=True): cv.boolean,   # safety: must opt in to write
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    server: FoxessServer = hass.data[DOMAIN].pop(entry.entry_id, None)
    if server:
        await server.stop()
    return unloaded
