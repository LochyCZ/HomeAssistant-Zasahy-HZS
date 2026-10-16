"""Zásahy HZS – výjezdy hasičů z Přehledu událostí HZS (WebOhled)."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import WebohledError
from .const import CONF_KRAJ_ID, CONF_MAPA, USER_AGENT
from .coordinator import HzsConfigEntry, HzsCoordinator
from .zdroje import vytvor_api


async def async_setup_entry(hass: HomeAssistant, entry: HzsConfigEntry) -> bool:
    api = vytvor_api(async_get_clientsession(hass), entry.data, USER_AGENT)
    try:
        cis = await api.ciselniky(entry.data.get(CONF_KRAJ_ID))
    except WebohledError as err:
        raise ConfigEntryNotReady(f"Číselníky se nepodařilo načíst: {err}") from err

    coordinator = HzsCoordinator(hass, entry, cis, api)
    await coordinator.async_config_entry_first_refresh()
    coordinator.platformy = _platformy(entry)
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, coordinator.platformy)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HzsConfigEntry) -> bool:
    # Odpojit platformy, se kterými se entry načetla (volby se mezitím mohly změnit)
    return await hass.config_entries.async_unload_platforms(entry, entry.runtime_data.platformy)


async def _async_reload(hass: HomeAssistant, entry: HzsConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _platformy(entry: HzsConfigEntry) -> list[Platform]:
    platformy = [Platform.SENSOR]
    if entry.options.get(CONF_MAPA, True):
        platformy.append(Platform.GEO_LOCATION)
    return platformy
