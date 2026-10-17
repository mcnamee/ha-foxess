"""The FoxESS Local integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DEFAULT_PORT, DOMAIN, SIGNAL_UPDATE
from .protocol import derive_virtual
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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    server: FoxessServer = hass.data[DOMAIN].pop(entry.entry_id, None)
    if server:
        await server.stop()
    return unloaded
