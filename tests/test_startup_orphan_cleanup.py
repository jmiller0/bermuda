"""
Regression tests: the startup orphan cleanup must not run against an empty device list.

async_setup_entry() runs one refresh and then async_cleanup_orphaned_entities().
While the Bluetooth ID data is still loading, _async_update_data() returns early
without building coordinator.devices, so the cleanup saw an empty list and removed
every Bermuda entity from the entity registry on each start (they were recreated
seconds later, but the "remove" events reached listeners such as Proximity).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.const import DOMAIN

from .test_coordinator_methods import _make_coordinator

ADDRESS = "aa:bb:cc:dd:ee:ff"


def _setup(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    coordinator = _make_coordinator(hass)
    coordinator.config_entry = entry
    registry = er.async_get(hass)
    tracker = registry.async_get_or_create(
        "device_tracker", DOMAIN, ADDRESS, config_entry=entry, suggested_object_id="tracked_phone"
    )
    removed: list[str] = []
    hass.bus.async_listen(
        er.EVENT_ENTITY_REGISTRY_UPDATED,
        lambda event: removed.append(event.data["entity_id"]) if event.data["action"] == "remove" else None,
    )
    return coordinator, registry, tracker, removed


async def test_cleanup_skipped_while_bt_ids_loading(hass: HomeAssistant) -> None:
    """Pending ID load means devices were never built: nothing may be removed."""
    coordinator, registry, tracker, removed = _setup(hass)
    coordinator._waitingfor_load_manufacturer_ids = True

    await coordinator.async_cleanup_orphaned_entities()
    await hass.async_block_till_done()

    assert registry.async_get(tracker.entity_id) is not None
    assert removed == []


async def test_cleanup_skipped_with_empty_device_list(hass: HomeAssistant) -> None:
    """An empty device list is never evidence that every entity is orphaned."""
    coordinator, registry, tracker, removed = _setup(hass)
    assert coordinator.devices == {}

    await coordinator.async_cleanup_orphaned_entities()
    await hass.async_block_till_done()

    assert registry.async_get(tracker.entity_id) is not None
    assert removed == []


async def test_cleanup_still_removes_real_orphans(hass: HomeAssistant) -> None:
    """With a populated device list, entities of unknown addresses are still removed."""
    coordinator, registry, tracker, removed = _setup(hass)
    coordinator.devices = {"11:22:33:44:55:66": BermudaDevice("11:22:33:44:55:66", coordinator)}

    await coordinator.async_cleanup_orphaned_entities()
    await hass.async_block_till_done()

    assert registry.async_get(tracker.entity_id) is None
    assert removed == [tracker.entity_id]


async def test_cleanup_keeps_entities_of_known_devices(hass: HomeAssistant) -> None:
    """Entities whose device is in the list survive the cleanup."""
    coordinator, registry, tracker, removed = _setup(hass)
    coordinator.devices = {ADDRESS: BermudaDevice(ADDRESS, coordinator)}

    await coordinator.async_cleanup_orphaned_entities()
    await hass.async_block_till_done()

    assert registry.async_get(tracker.entity_id) is not None
    assert removed == []


async def test_wait_for_manufacturer_ids_awaits_load_task(hass: HomeAssistant) -> None:
    """Setup waits for the background ID load before the first refresh."""
    coordinator = _make_coordinator(hass)
    done = asyncio.Event()

    async def _load() -> None:
        await asyncio.sleep(0.05)
        coordinator._waitingfor_load_manufacturer_ids = False
        done.set()

    coordinator._waitingfor_load_manufacturer_ids = True
    coordinator._load_manufacturer_ids_task = hass.async_create_task(_load())

    try:
        await coordinator.async_wait_for_manufacturer_ids()
    finally:
        await coordinator._load_manufacturer_ids_task

    assert done.is_set()
    assert coordinator._waitingfor_load_manufacturer_ids is False


async def test_wait_for_manufacturer_ids_without_task(hass: HomeAssistant) -> None:
    """Coordinators built without the task (tests, partial init) do not fail."""
    coordinator = _make_coordinator(hass)
    await coordinator.async_wait_for_manufacturer_ids()


async def test_wait_for_manufacturer_ids_times_out(hass: HomeAssistant) -> None:
    """A stuck load does not block setup forever."""
    coordinator = _make_coordinator(hass)
    never = asyncio.Event()
    coordinator._load_manufacturer_ids_task = hass.async_create_task(never.wait())
    try:
        await coordinator.async_wait_for_manufacturer_ids(max_wait=0.05)
        assert not coordinator._load_manufacturer_ids_task.done()
    finally:
        coordinator._load_manufacturer_ids_task.cancel()
