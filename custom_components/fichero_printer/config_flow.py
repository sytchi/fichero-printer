"""Config flow for Fichero Label Printer."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.helpers import selector

from .const import (
    CONF_ADDRESS,
    CONF_AUTO_CONNECT,
    CONF_DENSITY,
    CONF_LABEL_LENGTH,
    CONF_POWER_OFF_ON_DISCONNECT,
    CONF_STARTUP_DELAY,
    CONF_SWITCHBOT_ENTITY,
    DEFAULT_AUTO_CONNECT,
    DEFAULT_DENSITY,
    DEFAULT_LABEL_LENGTH,
    DEFAULT_STARTUP_DELAY,
    DOMAIN,
)


class FicheroConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a Fichero printer without requiring it to be awake."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            unique_id = user_input.get(CONF_ADDRESS) or user_input[CONF_NAME]
            await self.async_set_unique_id(unique_id.lower())
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Fichero D11s"): str,
                vol.Optional(CONF_ADDRESS, default=""): str,
                vol.Required(CONF_SWITCHBOT_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["switch", "button", "input_button"])
                ),
                vol.Required(CONF_STARTUP_DELAY, default=DEFAULT_STARTUP_DELAY): vol.All(
                    vol.Coerce(float), vol.Range(min=0, max=30)
                ),
                vol.Required(CONF_LABEL_LENGTH, default=DEFAULT_LABEL_LENGTH): vol.All(
                    vol.Coerce(int), vol.Range(min=10, max=100)
                ),
                vol.Required(CONF_DENSITY, default=DEFAULT_DENSITY): vol.In([0, 1, 2]),
                vol.Required(CONF_POWER_OFF_ON_DISCONNECT, default=True): bool,
                vol.Required(CONF_AUTO_CONNECT, default=DEFAULT_AUTO_CONNECT): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)
