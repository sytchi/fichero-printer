"""Config flow for Fichero Label Printer."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ADDRESS,
    CONF_AI_IMAGE_ENTITY,
    CONF_AI_TASK_ENTITY,
    CONF_DENSITY,
    CONF_LABEL_LENGTH,
    CONF_POWER_OFF_ON_DISCONNECT,
    CONF_STARTUP_DELAY,
    CONF_SWITCHBOT_ENTITY,
    DEFAULT_DENSITY,
    DEFAULT_LABEL_LENGTH,
    DEFAULT_STARTUP_DELAY,
    DOMAIN,
    MAX_LABEL_LENGTH,
    MIN_LABEL_LENGTH,
)

AI_TASK_ENTITIES = (CONF_AI_TASK_ENTITY, CONF_AI_IMAGE_ENTITY)


def build_schema(current: dict, *, include_name: bool = True) -> vol.Schema:
    """The settings form, filled in with what the printer already uses."""
    fields: dict = {}
    if include_name:
        fields[vol.Required(CONF_NAME, default=current.get(CONF_NAME, "Fichero D11s"))] = str
    fields[vol.Optional(CONF_ADDRESS, default=current.get(CONF_ADDRESS, ""))] = str
    switchbot = (
        vol.Required(CONF_SWITCHBOT_ENTITY, description={"suggested_value": current.get(CONF_SWITCHBOT_ENTITY)})
        if current
        else vol.Required(CONF_SWITCHBOT_ENTITY)
    )
    fields[switchbot] = selector.EntitySelector(
        selector.EntitySelectorConfig(domain=["switch", "button", "input_button"])
    )
    fields[
        vol.Required(CONF_STARTUP_DELAY, default=current.get(CONF_STARTUP_DELAY, DEFAULT_STARTUP_DELAY))
    ] = vol.All(vol.Coerce(float), vol.Range(min=0, max=30))
    fields[
        vol.Required(CONF_LABEL_LENGTH, default=current.get(CONF_LABEL_LENGTH, DEFAULT_LABEL_LENGTH))
    ] = vol.All(vol.Coerce(int), vol.Range(min=MIN_LABEL_LENGTH, max=MAX_LABEL_LENGTH))
    fields[vol.Required(CONF_DENSITY, default=current.get(CONF_DENSITY, DEFAULT_DENSITY))] = vol.In([0, 1, 2])
    fields[
        vol.Required(CONF_POWER_OFF_ON_DISCONNECT, default=current.get(CONF_POWER_OFF_ON_DISCONNECT, True))
    ] = bool
    for key in AI_TASK_ENTITIES:
        # An entity picker keeps its current value through the suggestion, so
        # reopening the form does not silently clear it.
        marker = vol.Optional(key, description={"suggested_value": current.get(key)})
        fields[marker] = selector.EntitySelector(selector.EntitySelectorConfig(domain="ai_task"))
    return vol.Schema(fields)


class FicheroConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a Fichero printer without requiring it to be awake."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            unique_id = user_input.get(CONF_ADDRESS) or user_input[CONF_NAME]
            await self.async_set_unique_id(unique_id.lower())
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        return self.async_show_form(step_id="user", data_schema=build_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return FicheroOptionsFlow()


class FicheroOptionsFlow(config_entries.OptionsFlow):
    """Change the settings in place instead of deleting and adding the printer."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            data = {**self.config_entry.data, **user_input}
            # A cleared entity picker comes back missing rather than empty, so
            # the old value has to be dropped explicitly.
            for key in AI_TASK_ENTITIES:
                if key not in user_input:
                    data.pop(key, None)
            self.hass.config_entries.async_update_entry(self.config_entry, data=data)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=build_schema(dict(self.config_entry.data), include_name=False),
        )
