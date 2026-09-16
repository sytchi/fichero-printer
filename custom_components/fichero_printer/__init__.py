"""Home Assistant integration for the Fichero/D11s label printer."""

from __future__ import annotations

import base64
from functools import partial
from pathlib import Path

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    CARD_URL,
    CONF_AI_TASK_ENTITY,
    CONF_LABEL_LENGTH,
    DEFAULT_MARGIN_MM,
    DOMAIN,
    PLATFORMS,
    SERVICE_CONNECT,
    SERVICE_DELETE_FAVORITE,
    SERVICE_DISCONNECT,
    SERVICE_PRINT,
    SERVICE_SAVE_FAVORITE,
)
from .manager import FicheroManager
from .render import (
    DEFAULT_ICON_SIDE,
    DOTS_PER_MM,
    ICON_SIDES,
    icon_character,
    render_preview_png,
)

SERVICE_ENTRY_SCHEMA = vol.Schema({vol.Required("config_entry_id"): cv.string})
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
SERVICE_PRINT_SCHEMA = SERVICE_ENTRY_SCHEMA.extend(
    {
        vol.Optional("text", default=""): cv.string,
        vol.Optional("copies", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
        vol.Optional("margin_mm", default=DEFAULT_MARGIN_MM): vol.All(vol.Coerce(float), vol.Range(min=0, max=5)),
        vol.Optional("offset_mm", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=-10, max=10)),
        vol.Optional("date", default=""): cv.string,
        vol.Optional("icon", default=""): cv.string,
        vol.Optional("icon_side", default=DEFAULT_ICON_SIDE): vol.In(ICON_SIDES),
    }
)
SERVICE_SAVE_FAVORITE_SCHEMA = SERVICE_ENTRY_SCHEMA.extend({vol.Required("text"): cv.string})
SERVICE_DELETE_FAVORITE_SCHEMA = SERVICE_ENTRY_SCHEMA.extend(
    {
        vol.Exclusive("text", "favorite"): cv.string,
        vol.Exclusive("index", "favorite"): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)


def _is_icon(name: str) -> bool:
    try:
        icon_character(name)
    except ValueError:
        return False
    return True


ICON_INSTRUCTIONS = (
    "Pick the single Material Design Icons (MDI) icon that best illustrates this "
    "label for a household storage container. Answer with the bare icon name as "
    "used in Home Assistant without the mdi: prefix, for example pasta, "
    "bowl-mix, cupcake or fridge-outline. The name must be a real MDI icon. "
    "Label text: "
)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "fichero_printer/suggest_icon",
        vol.Required("config_entry_id"): cv.string,
        vol.Required("text"): cv.string,
    }
)
@websocket_api.async_response
async def websocket_suggest_icon(hass: HomeAssistant, connection, msg: dict) -> None:
    """Ask the configured AI Task entity for an icon that fits the label."""
    entry = hass.config_entries.async_get_entry(msg["config_entry_id"])
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(
            msg["id"], "not_found", "Fichero printer configuration is not loaded"
        )
        return
    ai_task_entity = entry.data.get(CONF_AI_TASK_ENTITY)
    if not ai_task_entity:
        connection.send_error(
            msg["id"],
            "not_configured",
            "No AI Task entity is configured for this printer",
        )
        return
    if not msg["text"].strip():
        connection.send_error(msg["id"], "invalid_label", "Label text cannot be empty")
        return
    try:
        result = await hass.services.async_call(
            "ai_task",
            "generate_data",
            {
                "entity_id": ai_task_entity,
                "task_name": "Fichero label icon",
                "instructions": ICON_INSTRUCTIONS + msg["text"].strip(),
                "structure": {"icon": {"selector": {"text": {}}}},
            },
            blocking=True,
            return_response=True,
        )
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "ai_task_failed", str(err))
        return
    suggestion = str((result or {}).get("data", {}).get("icon", "")).strip()
    if not _is_icon(suggestion):
        # Models reach for plausible names that do not exist, so give one
        # correction round with the rejected name spelled out.
        try:
            result = await hass.services.async_call(
                "ai_task",
                "generate_data",
                {
                    "entity_id": ai_task_entity,
                    "task_name": "Fichero label icon",
                    "instructions": (
                        f"{ICON_INSTRUCTIONS}{msg['text'].strip()}\n"
                        f"The name {suggestion or 'you gave'} does not exist in MDI. "
                        "Pick a different, existing MDI icon name."
                    ),
                    "structure": {"icon": {"selector": {"text": {}}}},
                },
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            connection.send_error(msg["id"], "ai_task_failed", str(err))
            return
        suggestion = str((result or {}).get("data", {}).get("icon", "")).strip()
    if not _is_icon(suggestion):
        connection.send_error(
            msg["id"],
            "invalid_icon",
            f"The AI Task suggested {suggestion or 'nothing'}, which is not an MDI icon",
        )
        return
    connection.send_result(msg["id"], {"icon": f"mdi:{suggestion.removeprefix('mdi:')}"})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "fichero_printer/preview",
        vol.Required("config_entry_id"): cv.string,
        vol.Optional("text", default=""): cv.string,
        vol.Optional("date", default=""): cv.string,
        vol.Optional("icon", default=""): cv.string,
        vol.Optional("icon_side", default=DEFAULT_ICON_SIDE): vol.In(ICON_SIDES),
        vol.Optional("margin_mm", default=DEFAULT_MARGIN_MM): vol.All(vol.Coerce(float), vol.Range(min=0, max=5)),
        vol.Optional("offset_mm", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=-10, max=10)),
    }
)
@websocket_api.async_response
async def websocket_preview(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return the label as a PNG, rendered by the same code that prints it."""
    entry = hass.config_entries.async_get_entry(msg["config_entry_id"])
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(
            msg["id"], "not_found", "Fichero printer configuration is not loaded"
        )
        return
    try:
        png = await hass.async_add_executor_job(
            partial(
                render_preview_png,
                msg["text"],
                entry.data[CONF_LABEL_LENGTH] * DOTS_PER_MM,
                margin_dots=round(msg["margin_mm"] * DOTS_PER_MM),
                offset_dots=round(msg["offset_mm"] * DOTS_PER_MM),
                date=msg["date"],
                icon=msg["icon"],
                icon_side=msg["icon_side"],
            )
        )
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_label", str(err))
        return
    connection.send_result(msg["id"], {"png": base64.b64encode(png).decode("ascii")})


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Register services and the bundled dashboard card once."""
    frontend_file = Path(__file__).parent / "frontend" / "fichero-printer-card.js"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(frontend_file), cache_headers=False)]
    )
    # The query string changes with releases so dashboards cannot keep serving
    # an older card script from the browser cache after a HACS update.
    add_extra_js_url(hass, f"{CARD_URL}?v=0.1.10")

    def manager_for(call: ServiceCall) -> FicheroManager:
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        if entry is None or entry.domain != DOMAIN or entry.runtime_data is None:
            raise HomeAssistantError("Fichero printer configuration is not loaded")
        return entry.runtime_data

    async def handle_connect(call: ServiceCall) -> None:
        await manager_for(call).async_connect()

    async def handle_disconnect(call: ServiceCall) -> None:
        await manager_for(call).async_disconnect()

    async def handle_print(call: ServiceCall) -> None:
        await manager_for(call).async_print(
            call.data["text"],
            call.data["copies"],
            call.data["margin_mm"],
            call.data["offset_mm"],
            call.data["date"],
            call.data["icon"],
            call.data["icon_side"],
        )

    async def handle_save(call: ServiceCall) -> None:
        await manager_for(call).async_save_favorite(call.data["text"])

    async def handle_delete(call: ServiceCall) -> None:
        await manager_for(call).async_delete_favorite(
            text=call.data.get("text"), index=call.data.get("index")
        )

    hass.services.async_register(DOMAIN, SERVICE_CONNECT, handle_connect, schema=SERVICE_ENTRY_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_DISCONNECT, handle_disconnect, schema=SERVICE_ENTRY_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_PRINT, handle_print, schema=SERVICE_PRINT_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SAVE_FAVORITE, handle_save, schema=SERVICE_SAVE_FAVORITE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_DELETE_FAVORITE, handle_delete, schema=SERVICE_DELETE_FAVORITE_SCHEMA)
    websocket_api.async_register_command(hass, websocket_preview)
    websocket_api.async_register_command(hass, websocket_suggest_icon)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one configured printer."""
    manager = FicheroManager(hass, entry)
    await manager.async_load()
    entry.runtime_data = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await manager.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Disconnect and unload one printer."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_stop()
    await entry.runtime_data.async_disconnect(power_off=False)
    return True
