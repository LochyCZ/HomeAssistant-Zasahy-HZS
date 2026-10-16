"""Společné věci pro entity."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import CONF_BASE_URL, DOMAIN
from .coordinator import HzsCoordinator


def device_info(coordinator: HzsCoordinator) -> DeviceInfo:
    entry = coordinator.config_entry
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer=f"HZS – {coordinator.ciselniky.kraj}",
        model="Přehled událostí HZS",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=entry.data[CONF_BASE_URL],
    )
