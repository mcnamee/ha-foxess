"""Config flow for FoxESS Local."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DEFAULT_PORT, DOMAIN


class FoxessLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-instance config flow; only the listen port is configurable."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="FoxESS KH (local)", data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required("port", default=DEFAULT_PORT): int}
            ),
        )
