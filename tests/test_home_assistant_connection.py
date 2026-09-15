"""Connection lifecycle regression tests with simulated HA and BLE boundaries."""

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def manager_module(monkeypatch):
    # Keep HA optional for the CLI suite; load the real manager with only its
    # HA boundary stubbed, and use the installed Bleak/connector implementations.
    modules = {
        name: ModuleType(name)
        for name in (
            "homeassistant", "homeassistant.components",
            "homeassistant.components.bluetooth", "homeassistant.core",
            "homeassistant.exceptions", "homeassistant.helpers",
            "homeassistant.helpers.storage", "fichero_test_integration",
        )
    }
    root = Path(__file__).parents[1] / "custom_components" / "fichero_printer"
    modules["fichero_test_integration"].__path__ = [str(root)]
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.exceptions"].HomeAssistantError = type("HomeAssistantError", (Exception,), {})
    modules["homeassistant.helpers.storage"].Store = Mock()
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    name = "fichero_test_integration.manager"
    spec = importlib.util.spec_from_file_location(name, root / "manager.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def session(manager_module):
    target = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name="FICHERO_test")
    manager_module.bluetooth.async_ble_device_from_address = Mock(return_value=target)
    manager = manager_module.FicheroManager(Mock(), SimpleNamespace(entry_id="test", data={}))
    manager._press_switchbot = AsyncMock()
    return manager, target


def client(missing=False):
    return SimpleNamespace(
        is_connected=True,
        services=SimpleNamespace(get_characteristic=Mock(return_value=None if missing else object())),
        start_notify=AsyncMock(), clear_cache=AsyncMock(), disconnect=AsyncMock(),
    )


def test_recovers_missing_gatt_services(manager_module, session):
    manager, target = session
    stale, fresh = client(missing=True), client()
    manager_module.establish_connection = AsyncMock(side_effect=[stale, fresh])
    asyncio.run(manager._connect_to_printer(target))
    stale.clear_cache.assert_awaited_once()
    stale.disconnect.assert_awaited_once()
    stale.start_notify.assert_not_awaited()
    assert manager.client is fresh
    assert manager.status == "connected"
    calls = manager_module.establish_connection.call_args_list
    assert calls[0].kwargs["use_services_cache"] is True
    assert calls[1].kwargs["use_services_cache"] is False
    assert manager_module.bluetooth.async_ble_device_from_address.call_count == 2


def test_missing_services_retry_is_bounded(manager_module, session):
    manager, target = session
    first, second = client(True), client(True)
    manager_module.establish_connection = AsyncMock(side_effect=[first, second])
    with pytest.raises(manager_module.HomeAssistantError, match="GATT services unavailable"):
        asyncio.run(manager._connect_to_printer(target))
    assert manager.client is None
    first.disconnect.assert_awaited_once()
    second.disconnect.assert_awaited_once()
    second.clear_cache.assert_not_awaited()


@pytest.mark.parametrize("error", [RuntimeError("notify failed"), asyncio.CancelledError()])
def test_notification_failure_or_cancellation_releases_client(manager_module, session, error):
    manager, target = session
    connection = client()
    connection.start_notify.side_effect = error
    manager_module.establish_connection = AsyncMock(return_value=connection)
    with pytest.raises(type(error)):
        asyncio.run(manager._connect_to_printer(target))
    connection.disconnect.assert_awaited_once()
    assert manager.client is None


def test_old_disconnect_callback_does_not_clear_new_session(session):
    manager, _ = session
    old, current = client(), client()
    manager.client = current
    manager._on_disconnect(old)
    assert manager.client is current
    manager._on_disconnect(current)
    assert manager.client is None


def test_unload_releases_ble_without_switchbot(session):
    manager, _ = session
    connection = client()
    manager.client = connection
    asyncio.run(manager.async_disconnect(power_off=False))
    connection.disconnect.assert_awaited_once()
    manager._press_switchbot.assert_not_awaited()
    assert manager.client is None
    assert manager.status == "disconnected"


def test_success_does_not_clear_cache_or_disconnect(manager_module, session):
    manager, target = session
    connection = client()
    manager_module.establish_connection = AsyncMock(return_value=connection)
    asyncio.run(manager._connect_to_printer(target))
    connection.clear_cache.assert_not_awaited()
    connection.disconnect.assert_not_awaited()
    assert manager.connected


@pytest.mark.parametrize("reason", [
    ("org.bluez.Error.BREDR.ProfileUnavailable", "No more profiles to connect to"),
    ("org.bluez.Error.Failed", "br-connection-not-supported"),
    ("org.bluez.Error.Failed", "br-connection-profile-unavailable"),
])
@pytest.mark.parametrize("wrapped", [False, True])
def test_bredr_failure_selects_le_and_retries(manager_module, session, wrapped, reason):
    manager, target = session
    error = manager_module.BleakDBusError(reason[0], [reason[1]])
    if wrapped:
        error = manager_module.BleakError(f"Connection failed: {error}")
    connection = client()
    manager_module.establish_connection = AsyncMock(side_effect=[error, connection])
    manager_module.prefer_le = AsyncMock(return_value=True)
    asyncio.run(manager._connect_to_printer(target))
    manager_module.prefer_le.assert_awaited_once_with(target)
    assert manager.connected


def test_bredr_unsupported_reports_actionable_error(manager_module, session):
    manager, target = session
    manager_module.establish_connection = AsyncMock(side_effect=manager_module.BleakDBusError(
        "org.bluez.Error.BREDR.ProfileUnavailable", ["No more profiles to connect to"]))
    manager_module.prefer_le = AsyncMock(return_value=False)
    with pytest.raises(manager_module.HomeAssistantError, match="PreferredBearer"):
        asyncio.run(manager._connect_to_printer(target))
    assert manager_module.establish_connection.await_count == 1


def test_switchbot_press_actually_connects_without_monitor(manager_module, session, monkeypatch):
    manager, target = session
    manager.entry.data = {"startup_delay": 0}
    manager._resolve_printer = AsyncMock(return_value=target)
    connection = client()
    manager_module.establish_connection = AsyncMock(return_value=connection)
    asyncio.run(manager.async_connect())
    manager._press_switchbot.assert_awaited_once()
    assert manager.connected
    # Another connect must not turn an already-connected printer off.
    asyncio.run(manager.async_connect())
    assert manager._press_switchbot.await_count == 1


def test_wake_failure_preserves_bluez_error(manager_module, session):
    manager, target = session
    manager.entry.data = {"startup_delay": 0}
    manager._resolve_printer = AsyncMock(return_value=target)
    manager._connect_to_printer = AsyncMock(side_effect=RuntimeError("BlueZ diagnostic"))
    with pytest.raises(manager_module.HomeAssistantError, match="BlueZ diagnostic"):
        asyncio.run(manager.async_connect())
    assert manager.last_error == "BlueZ diagnostic"
    manager._press_switchbot.assert_awaited_once()


def test_switchbot_press_does_not_wait_for_background_connection_lock(session):
    manager, _ = session
    manager.entry.data = {"startup_delay": 0}

    async def run():
        await manager._operation_lock.acquire()
        task = asyncio.create_task(manager.async_connect())
        try:
            # The service call must happen while the background operation still
            # owns the Bluetooth lock.
            for _ in range(10):
                if manager._press_switchbot.await_count:
                    break
                await asyncio.sleep(0)
            manager._press_switchbot.assert_awaited_once()
            assert manager._operation_lock.locked()
            assert not task.done()
        finally:
            manager._operation_lock.release()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_concurrent_wake_requests_only_press_once(session):
    manager, _ = session
    manager.entry.data = {"startup_delay": 0}
    connected = asyncio.Event()

    async def connect(_target):
        manager.client = SimpleNamespace(is_connected=True)
        connected.set()

    async def run():
        manager._resolve_printer = AsyncMock(return_value=object())
        manager._connect_to_printer = connect
        await asyncio.gather(manager.async_connect(), manager.async_connect())
        await connected.wait()

    asyncio.run(run())
    manager._press_switchbot.assert_awaited_once()


@pytest.mark.parametrize("fails", [False, True])
def test_bluez_sets_only_target_preferred_bearer(manager_module, monkeypatch, fails):
    import dbus_fast
    import dbus_fast.aio
    bluez = sys.modules["fichero_test_integration.bluez"]
    monkeypatch.setattr(bluez.sys, "platform", "linux")
    bus = SimpleNamespace(connect=AsyncMock(), disconnect=Mock(), call=AsyncMock(return_value=SimpleNamespace(
        message_type=dbus_fast.MessageType.ERROR if fails else dbus_fast.MessageType.METHOD_RETURN,
        error_name="org.freedesktop.DBus.Error.UnknownProperty", body=[])))
    factory = Mock(return_value=bus)
    monkeypatch.setattr(dbus_fast.aio, "MessageBus", factory)
    device = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", details={"path": "/org/bluez/hci1/dev_AA_BB_CC_DD_EE_FF"})
    assert asyncio.run(bluez.prefer_le(device)) is (not fails)
    message = bus.call.call_args.args[0]
    assert message.path == device.details["path"]
    assert message.member == "Set"
    assert message.body[:2] == ["org.bluez.Device1", "PreferredBearer"]
    assert message.body[2].value == "le"
    bus.disconnect.assert_called_once()


def test_bluez_never_changes_local_host_for_proxy(manager_module, monkeypatch):
    import dbus_fast.aio
    bluez = sys.modules["fichero_test_integration.bluez"]
    monkeypatch.setattr(bluez.sys, "platform", "linux")
    factory = Mock()
    monkeypatch.setattr(dbus_fast.aio, "MessageBus", factory)
    device = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", details={"source": "esphome-proxy"})
    assert asyncio.run(bluez.prefer_le(device)) is False
    factory.assert_not_called()


def test_monitor_does_not_join_startup_tasks_and_stops_on_unload(session):
    manager, _ = session

    async def run():
        started = asyncio.Event()
        startup_tasks = []
        background_tasks = []

        async def monitor():
            started.set()
            await asyncio.Event().wait()

        def create_startup_task(coro, *args):
            task = asyncio.create_task(coro)
            startup_tasks.append(task)
            return task

        def create_background_task(hass, coro, name):
            assert hass is manager.hass
            task = asyncio.create_task(coro, name=name)
            background_tasks.append(task)
            return task

        manager.hass.async_create_task = create_startup_task
        manager.entry.async_create_background_task = create_background_task
        manager._connection_monitor = monitor
        try:
            await manager.async_start()
            await asyncio.wait_for(started.wait(), 1)
            # A permanent monitor must not be awaited by HA's startup barrier.
            assert not startup_tasks
            await manager.async_start()
            assert len(background_tasks) == 1
            await manager.async_stop()
            assert background_tasks[0].cancelled()
            assert manager._monitor_task is None
        finally:
            for task in startup_tasks + background_tasks:
                task.cancel()
            await asyncio.gather(*startup_tasks, *background_tasks, return_exceptions=True)

    asyncio.run(run())


def test_delete_favorite_by_card_index(session):
    manager, _ = session
    manager.favorites = ["Kitchen", "Garage", "Office"]
    previously_published = manager.favorites
    manager._store = SimpleNamespace(async_save=AsyncMock())
    listener = Mock()
    manager.add_listener(listener)

    asyncio.run(manager.async_delete_favorite(index=1))

    assert manager.favorites == ["Kitchen", "Office"]
    assert previously_published == ["Kitchen", "Garage", "Office"]
    manager._store.async_save.assert_awaited_once_with(
        {"favorites": ["Kitchen", "Office"]}
    )
    listener.assert_called_once()


def test_save_favorite_replaces_published_list(session):
    manager, _ = session
    manager.favorites = ["Kitchen"]
    previously_published = manager.favorites
    manager._store = SimpleNamespace(async_save=AsyncMock())
    listener = Mock()
    manager.add_listener(listener)

    asyncio.run(manager.async_save_favorite("Garage"))

    assert manager.favorites == ["Kitchen", "Garage"]
    assert previously_published == ["Kitchen"]
    manager._store.async_save.assert_awaited_once_with(
        {"favorites": ["Kitchen", "Garage"]}
    )
    listener.assert_called_once()


def test_delete_missing_favorite_reports_error(manager_module, session):
    manager, _ = session
    manager.favorites = ["Kitchen"]
    manager._store = SimpleNamespace(async_save=AsyncMock())

    with pytest.raises(manager_module.HomeAssistantError, match="no longer exists"):
        asyncio.run(manager.async_delete_favorite(index=4))

    manager._store.async_save.assert_not_awaited()


@pytest.mark.parametrize(("data", "expected"), [
    ({"auto_connect": False}, False),
    ({"auto_connect": True}, True),
    ({}, True),  # entries created before the option keep connecting
])
def test_monitor_connects_only_with_auto_connect(session, data, expected):
    manager, target = session
    manager.entry.data = data
    manager._visible_printer = Mock(return_value=target)
    manager._connect_to_printer = AsyncMock()

    async def run():
        task = asyncio.create_task(manager._connection_monitor())
        await asyncio.sleep(0.05)
        manager._monitor_stop.set()
        await asyncio.wait_for(task, 1)

    asyncio.run(run())
    assert manager._connect_to_printer.await_count == (1 if expected else 0)
    assert manager._visible_printer.called is expected
