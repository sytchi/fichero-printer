"""Persistent BLE session and label rendering for Fichero/D11s printers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging

from bleak import BleakClient
from bleak.exc import BleakCharacteristicNotFoundError, BleakDBusError, BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .bluez import prefer_le
from .const import (
    CONF_ADDRESS,
    CONF_DENSITY,
    CONF_LABEL_LENGTH,
    CONF_POWER_OFF_ON_DISCONNECT,
    CONF_STARTUP_DELAY,
    CONF_SWITCHBOT_ENTITY,
    DEFAULT_MARGIN_MM,
    DEFAULT_STARTUP_DELAY,
)
from .render import render_text_raster

_LOGGER = logging.getLogger(__name__)
WRITE_UUID = "00002af1-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "00002af0-0000-1000-8000-00805f9b34fb"
PRINTHEAD_PX = 96
BYTES_PER_ROW = 12
DOTS_PER_MM = 8
NAME_PREFIXES = ("FICHERO", "D11s_")
UART_SERVICE_UUIDS = {
    "000018f0-0000-1000-8000-00805f9b34fb",
    "0000ff00-0000-1000-8000-00805f9b34fb",
    "e7810a71-73ae-499d-8c15-faa9aef0c3f2",
    "49535343-fe7d-4ae5-8fa9-9fafd205e455",
}


class FicheroManager:
    """Own one printer session and its persisted favorites."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self.client: BleakClient | None = None
        self._buffer = bytearray()
        self._response = asyncio.Event()
        self._operation_lock = asyncio.Lock()
        self._wake_lock = asyncio.Lock()
        self._listeners: set[Callable[[], None]] = set()
        self._store = Store(hass, 1, f"fichero_printer.{entry.entry_id}")
        self.favorites: list[str] = []
        self.status = "disconnected"
        self.last_error: str | None = None
        self._monitor_task: asyncio.Task | None = None
        self._monitor_stop = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self.client is not None and self.client.is_connected

    async def async_start(self) -> None:
        """Start permanently monitoring the printer connection."""
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_stop.clear()
            self._monitor_task = self.entry.async_create_background_task(
                self.hass,
                self._connection_monitor(),
                "Fichero printer connection monitor",
            )

    async def async_stop(self) -> None:
        """Stop the background connection monitor."""
        self._monitor_stop.set()
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

    async def _connection_monitor(self) -> None:
        """Keep the HA connection status synchronized with the printer."""
        while not self._monitor_stop.is_set():
            try:
                if self.connected:
                    if self.status != "connected":
                        self._set_status("connected")
                elif not self._operation_lock.locked():
                    if self.status != "disconnected":
                        self._set_status("disconnected")

                    # Only try to connect when the printer is already
                    # advertising. Never press the SwitchBot from the monitor.
                    target = self._visible_printer()

                    if target is not None:
                        async with self._operation_lock:
                            if not self.connected:
                                try:
                                    await self._connect_to_printer(target)
                                except Exception as err:
                                    self.client = None
                                    self._set_status("disconnected", str(err))
                                    _LOGGER.debug(
                                        "Background printer connection failed: %s",
                                        err,
                                    )

            except asyncio.CancelledError:
                raise
            except Exception as err:
                self.client = None
                self._set_status("disconnected", str(err))
                _LOGGER.debug(
                    "Printer connection monitor error: %s",
                    err,
                )

            try:
                await asyncio.wait_for(
                    self._monitor_stop.wait(),
                    timeout=2,
                )
            except asyncio.TimeoutError:
                pass

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.favorites = list(data.get("favorites", []))

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def _notify(self) -> None:
        for listener in self._listeners:
            listener()

    def _set_status(self, status: str, error: str | None = None) -> None:
        self.status = status
        self.last_error = error
        self._notify()

    async def _press_switchbot(self) -> None:
        entity_id = self.entry.data[CONF_SWITCHBOT_ENTITY]
        domain = entity_id.split(".", 1)[0]
        if self.hass.states.get(entity_id) is None:
            raise HomeAssistantError(
                f"Configured SwitchBot entity {entity_id} does not exist"
            )
        # A SwitchBot Bot exposed as a switch is momentary: its on/off state is
        # not the printer state. Toggling guarantees one physical press even
        # when Home Assistant currently reports the switch as on.
        service = "press" if domain in ("button", "input_button") else "toggle"
        await self.hass.services.async_call(
            domain,
            service,
            {"entity_id": entity_id},
            blocking=True,
        )

    async def async_connect(self) -> None:
        """Wake the printer once and establish a usable BLE session."""
        await self._wake_and_wait_for_connection()

    async def _wake_and_wait_for_connection(self) -> None:
        """Wake immediately, then serialize the Bluetooth connection work."""
        async with self._wake_lock:
            if self.connected:
                return
            try:
                # Do this before waiting for the Bluetooth operation lock. The
                # background monitor can hold that lock for several seconds.
                self._set_status("powering_on")
                await self._press_switchbot()
                await asyncio.sleep(self.entry.data.get(CONF_STARTUP_DELAY, DEFAULT_STARTUP_DELAY))
                async with self._operation_lock:
                    if self.connected:
                        return
                    self._set_status("connecting")
                    target = await self._resolve_printer(timeout=30)
                    await self._connect_to_printer(target)
            except Exception as err:
                self._set_status("disconnected", str(err))
                raise HomeAssistantError(f"Could not connect to the printer: {err}") from err

    async def _connect_to_printer(self, target) -> None:
        """Connect to the printer and establish notifications."""
        for attempt in range(2):
            client = None
            ready = False
            try:
                # Resolve the same address again after cache recovery: HA may
                # now route it through another adapter or Bluetooth proxy.
                device = bluetooth.async_ble_device_from_address(
                    self.hass, target.address, connectable=True
                ) or target
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    device.name or device.address,
                    disconnected_callback=self._on_disconnect,
                    max_attempts=3,
                    use_services_cache=attempt == 0,
                )
                for uuid in (WRITE_UUID, NOTIFY_UUID):
                    if client.services.get_characteristic(uuid) is None:
                        raise BleakCharacteristicNotFoundError(uuid)
                await client.start_notify(NOTIFY_UUID, self._on_notify)
                if not client.is_connected:
                    raise HomeAssistantError("Printer disconnected during notification setup")
                self.client = client
                ready = True
                self._set_status("connected")
                _LOGGER.debug("Fichero printer connected: %s", device)
                return
            except (BleakError, KeyError) as err:
                if any(reason in str(err) for reason in (
                    "org.bluez.Error.BREDR.ProfileUnavailable",
                    "br-connection-profile-unavailable",
                    "br-connection-not-supported",
                )):
                    if attempt or not await prefer_le(device):
                        raise HomeAssistantError(
                            f"BlueZ selected Bluetooth Classic for {target.address}, but this "
                            "integration requires BLE. Set the printer's PreferredBearer to le "
                            "on the Bluetooth host, or use a connectable ESPHome BLE proxy. "
                            f"Original error: {err}"
                        ) from err
                    continue
                if not isinstance(err, (BleakCharacteristicNotFoundError, BleakDBusError, KeyError)):
                    raise
                if isinstance(err, BleakDBusError) and err.dbus_error not in {
                    "org.freedesktop.DBus.Error.UnknownObject",
                    "org.freedesktop.DBus.Error.UnknownMethod",
                }:
                    raise
                if attempt or client is None:
                    raise HomeAssistantError(
                        f"Printer GATT services unavailable for {target.address}: {err}"
                    ) from err
                _LOGGER.debug("Refreshing printer GATT cache for %s: %s", target.address, err)
                await client.clear_cache()
            finally:
                # Also release the connection when HA cancels setup on unload.
                if client is not None and not ready:
                    try:
                        await client.disconnect()
                    except Exception:
                        _LOGGER.debug("Failed to release printer connection", exc_info=True)

    async def _resolve_printer(self, timeout: float = 12):
        """Wait for HA discovery, including advertisements from BLE proxies."""
        address = self.entry.data.get(CONF_ADDRESS)
        if request_scan := getattr(bluetooth, "async_request_active_scan", None):
            await request_scan(self.hass)
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if device := self._visible_printer():
                return device
            await asyncio.sleep(0.5)

        target = address or "a device named FICHERO…/D11s_…"
        service_infos = bluetooth.async_discovered_service_info(
            self.hass, connectable=True
        )
        nearby = [
            self._describe_advertisement(info)
            for info in list(service_infos)[:8]
        ]
        scanner_count = bluetooth.async_scanner_count(
            self.hass, connectable=True
        )
        detail = f"{scanner_count} connectable Bluetooth scanner(s)"

        if nearby:
            detail += f"; nearby devices: {', '.join(nearby)}"
        else:
            detail += "; no connectable Bluetooth advertisements visible"

        raise HomeAssistantError(
            f"No connectable Fichero printer advertisement found for {target} "
            f"({detail})"
        )

    def _visible_printer(self):
        """Return a currently visible configured or name-matched printer."""
        address = self.entry.data.get(CONF_ADDRESS)

        if address and (
            device := bluetooth.async_ble_device_from_address(
                self.hass, address, connectable=True
            )
        ):
            return device

        for service_info in bluetooth.async_discovered_service_info(
            self.hass, connectable=True
        ):
            device = service_info.device

            if address and device.address.lower() == address.lower():
                return device

            name = service_info.name or device.name or ""

            if not address and name.lower().startswith(
                tuple(prefix.lower() for prefix in NAME_PREFIXES)
            ):
                return device

            advertised_services = {
                uuid.lower()
                for uuid in (service_info.service_uuids or [])
            }

            if not address and advertised_services & UART_SERVICE_UUIDS:
                return device

        return None

    @staticmethod
    def _describe_advertisement(service_info) -> str:
        """Build a compact diagnostic description for the dashboard card."""
        name = service_info.name or service_info.device.name or "unnamed"
        address = service_info.device.address
        services = [
            uuid.split("-", 1)[0]
            for uuid in (service_info.service_uuids or [])
        ]
        suffix = f"; services {','.join(services)}" if services else "; no services"
        return f"{name} [{address}{suffix}]"

    def _on_disconnect(self, _client) -> None:
        if _client is not self.client:
            return
        self.client = None
        self._set_status("disconnected")

    def _on_notify(self, _char, data: bytearray) -> None:
        self._buffer.extend(data)
        self._response.set()

    async def async_disconnect(self, power_off: bool = True) -> None:
        """Press SwitchBot directly."""
        if not power_off:
            # Unloading must release BlueZ/proxy resources without pressing
            # the physical power button (which could turn the printer on).
            async with self._operation_lock:
                client, self.client = self.client, None
                try:
                    if client is not None:
                        await client.disconnect()
                finally:
                    self._set_status("disconnected")
            return
        await self._press_switchbot()

    async def _send(self, data: bytes, wait: bool = False, timeout: float = 3) -> bytes:
        if not self.connected:
            raise HomeAssistantError("Printer disconnected during operation")
        if wait:
            self._buffer.clear()
            self._response.clear()
        await self.client.write_gatt_char(WRITE_UUID, data, response=False)
        if wait:
            try:
                await asyncio.wait_for(self._response.wait(), timeout)
                await asyncio.sleep(0.05)
            except TimeoutError as err:
                raise HomeAssistantError("Printer did not respond") from err
        return bytes(self._buffer)

    async def _send_chunked(self, data: bytes) -> None:
        for offset in range(0, len(data), 200):
            await self._send(data[offset : offset + 200])
            await asyncio.sleep(0.02)

    async def async_print(
        self,
        text: str,
        copies: int,
        margin_mm: float = DEFAULT_MARGIN_MM,
        offset_mm: float = 0.0,
        date: str = "",
    ) -> None:
        text = text.strip()
        date = date.strip()
        if not text and not date:
            raise HomeAssistantError("Label text cannot be empty")
        if not 1 <= copies <= 100:
            raise HomeAssistantError("Copies must be between 1 and 100")
        if not self.connected:
            await self._wake_and_wait_for_connection()

        async with self._operation_lock:
            if not self.connected:
                raise HomeAssistantError("Printer disconnected before printing")
            try:
                label_rows = self.entry.data[CONF_LABEL_LENGTH] * DOTS_PER_MM
                raster = render_text_raster(
                    text,
                    label_rows,
                    margin_dots=round(margin_mm * DOTS_PER_MM),
                    offset_dots=round(offset_mm * DOTS_PER_MM),
                    date=date,
                )
                await self._send(bytes([0x10, 0xFF, 0x10, 0, self.entry.data[CONF_DENSITY]]), True)
                await asyncio.sleep(0.1)
                for _ in range(copies):
                    status = await self._send(bytes([0x10, 0xFF, 0x40]), True)
                    if not status or status[-1] & 0x56:
                        raise HomeAssistantError("Printer is not ready (paper, cover, heat, or status error)")
                    await self._send(bytes([0x10, 0xFF, 0x84, 0]), True)
                    await self._send(b"\x00" * 12)
                    await self._send(bytes([0x10, 0xFF, 0xFE, 0x01]))
                    header = bytes([0x1D, 0x76, 0x30, 0, BYTES_PER_ROW, 0, label_rows & 0xFF, label_rows >> 8])
                    await self._send_chunked(header + raster)
                    await asyncio.sleep(0.5)
                    await self._send(bytes([0x1D, 0x0C]))
                    await asyncio.sleep(0.3)
                    await self._send(bytes([0x10, 0xFF, 0xFE, 0x45]), True, 60)
                self._set_status("connected")
            except Exception:
                self._set_status("disconnected")
                raise

    async def async_save_favorite(self, text: str) -> None:
        text = text.strip()
        if not text:
            raise HomeAssistantError("Favorite text cannot be empty")
        if text not in self.favorites:
            # Replace the list so HA's state machine can detect that the sensor
            # attributes changed. Mutating it in place also changed the list in
            # the previously published state and suppressed dashboard updates.
            self.favorites = [*self.favorites, text]
            await self._store.async_save({"favorites": self.favorites})
            self._notify()

    async def async_delete_favorite(
        self, text: str | None = None, index: int | None = None
    ) -> None:
        """Delete a favorite by stable card index or legacy text value."""
        favorites = list(self.favorites)
        if index is not None:
            if index >= len(favorites):
                raise HomeAssistantError("Favorite no longer exists")
            favorites.pop(index)
        elif text is not None and text in favorites:
            favorites.remove(text)
        else:
            raise HomeAssistantError("Favorite no longer exists")
        self.favorites = favorites
        await self._store.async_save({"favorites": self.favorites})
        self._notify()
