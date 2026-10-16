"""Zásahy jako geo_location entity – zobrazí se na mapě a fungují s triggerem geo_location."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import Udalost
from .const import DOMAIN, MAPA_HODIN
from .coordinator import HzsConfigEntry, HzsCoordinator

IKONY = {
    3100: "mdi:fire",
    3200: "mdi:car-emergency",
    3400: "mdi:biohazard",
    3500: "mdi:tools",
    3550: "mdi:lifebuoy",
    3800: "mdi:alarm-light-off-outline",
}


def _na_mape(c: HzsCoordinator) -> dict[int, Udalost]:
    hranice = dt_util.utcnow() - timedelta(hours=MAPA_HODIN)
    return {
        u.id: u
        for u in c.data.udalosti
        if u.latitude is not None and (u.aktivni or u.cas >= hranice)
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HzsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entity: dict[int, ZasahGeolocation] = {}

    @callback
    def _sync() -> None:
        aktualni = _na_mape(coordinator)
        for uid in list(entity):
            if uid not in aktualni:
                hass.async_create_task(entity.pop(uid).async_remove(force_remove=True))
        nove = []
        for uid, u in aktualni.items():
            if uid in entity:
                entity[uid].aktualizuj(u)
            else:
                entity[uid] = ZasahGeolocation(coordinator, u)
                nove.append(entity[uid])
        if nove:
            async_add_entities(nove)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class ZasahGeolocation(GeolocationEvent):
    _attr_should_poll = False
    _attr_source = DOMAIN
    _attr_unit_of_measurement = UnitOfLength.KILOMETERS

    def __init__(self, coordinator: HzsCoordinator, u: Udalost) -> None:
        self._coordinator = coordinator
        self._u = u
        self._nastav()

    def _nastav(self) -> None:
        u = self._u
        self._attr_name = u.nazev
        self._attr_icon = IKONY.get(u.typ_id, "mdi:alert")
        self._attr_latitude = u.latitude
        self._attr_longitude = u.longitude
        self._attr_distance = self._coordinator.vzdalenost(u)

    @callback
    def aktualizuj(self, u: Udalost) -> None:
        if u == self._u and u.technika == self._u.technika:
            return
        self._u = u
        self._nastav()
        if self.hass:
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        u = self._u
        return {
            "id": u.id,
            "typ": u.typ,
            "podtyp": u.podtyp,
            "stav": u.stav,
            "aktivni": u.aktivni,
            "obec": u.obec,
            "misto": u.misto,
            "poznamka": u.poznamka,
            "cas": u.cas.isoformat(),
            "jednotky": u.jednotky,
        }
