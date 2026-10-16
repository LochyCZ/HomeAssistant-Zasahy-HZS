"""Coordinator: stahuje události, doplňuje techniku a posílá eventy."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import Ciselniky, Technika, Udalost, WebohledError
from .zdroje import Zdroj
from .const import (
    CELY_KRAJ,
    CONF_OKRES,
    CONF_SCAN_INTERVAL,
    CONF_TECHNIKA,
    CONF_TYPY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_NOVY_ZASAH,
    EVENT_ZMENA_STAVU,
    OKNO_DNI,
)
from .krovak import vzdalenost_km

_LOGGER = logging.getLogger(__name__)

# Nejvýš tolik souběžných dotazů na techniku
_TECHNIKA_SOUBEZNE = 4

type HzsConfigEntry = ConfigEntry["HzsCoordinator"]


@dataclass
class HzsData:
    udalosti: list[Udalost] = field(default_factory=list)
    stazeno: datetime | None = None


class HzsCoordinator(DataUpdateCoordinator[HzsData]):
    """Pravidelně stahuje události HZS (WebOhled nebo Portál krizového řízení)."""

    config_entry: HzsConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: HzsConfigEntry,
        cis: Ciselniky,
        api: Zdroj,
    ) -> None:
        minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=minutes),
        )
        self.ciselniky = cis
        self.api = api
        okres = str(entry.data.get(CONF_OKRES, CELY_KRAJ))
        self.okres_id: int | None = None if okres == CELY_KRAJ else int(okres)
        self.typy: set[int] = {int(t) for t in entry.options.get(CONF_TYPY, [])}
        self.nacitat_techniku: bool = api.umi_techniku and entry.options.get(CONF_TECHNIKA, True)

        self._zname: dict[int, Udalost] = {}
        self._technika: dict[int, tuple[Technika, ...]] = {}
        self._technika_hotova: set[int] = set()
        self._prvni_beh = True
        self.platformy: list[str] = []

    async def _async_update_data(self) -> HzsData:
        now = dt_util.now()
        od = dt_util.start_of_local_day(now - timedelta(days=OKNO_DNI))
        do = now + timedelta(hours=1)
        try:
            udalosti = await self.api.udalosti(self.ciselniky, od, do, self.okres_id)
        except WebohledError as err:
            raise UpdateFailed(f"Nepodařilo se načíst události: {err}") from err

        if self.typy:
            udalosti = [u for u in udalosti if u.typ_id in self.typy]

        if self.nacitat_techniku:
            udalosti = await self._dopln_techniku(udalosti)

        self._zpracuj_eventy(udalosti)
        return HzsData(udalosti=udalosti, stazeno=now)

    async def _dopln_techniku(self, udalosti: list[Udalost]) -> list[Udalost]:
        """Technika se stahuje pro nové a probíhající zásahy a jednou po ukončení."""
        dnes = dt_util.start_of_local_day()
        k_nacteni = [
            u
            for u in udalosti
            if u.id not in self._technika_hotova
            and (u.aktivni or u.id not in self._zname or u.id in self._technika)
            and (not self._prvni_beh or u.aktivni or u.cas >= dnes)
        ]
        sem = asyncio.Semaphore(_TECHNIKA_SOUBEZNE)

        async def nacti(u: Udalost) -> None:
            async with sem:
                try:
                    self._technika[u.id] = await self.api.technika(u.id)
                except WebohledError as err:
                    _LOGGER.debug("Technika pro %s se nenačetla: %s", u.id, err)
                    return
            if not u.aktivni:
                self._technika_hotova.add(u.id)

        await asyncio.gather(*(nacti(u) for u in k_nacteni))

        platne = {u.id for u in udalosti}
        self._technika = {k: v for k, v in self._technika.items() if k in platne}
        self._technika_hotova &= platne

        return [replace(u, technika=self._technika.get(u.id, ())) for u in udalosti]

    def _zpracuj_eventy(self, udalosti: list[Udalost]) -> None:
        # První načtení po startu jen naplní paměť – žádná vlna notifikací po restartu.
        if not self._prvni_beh:
            for u in sorted(udalosti, key=lambda x: x.cas):
                predchozi = self._zname.get(u.id)
                if predchozi is None:
                    self.hass.bus.async_fire(EVENT_NOVY_ZASAH, self.event_data(u))
                elif predchozi.stav_id != u.stav_id:
                    data = self.event_data(u)
                    data["predchozi_stav"] = predchozi.stav
                    data["predchozi_stav_id"] = predchozi.stav_id
                    self.hass.bus.async_fire(EVENT_ZMENA_STAVU, data)
        self._prvni_beh = False
        self._zname = {u.id: u for u in udalosti}

    def vzdalenost(self, u: Udalost) -> float | None:
        if u.latitude is None or u.longitude is None:
            return None
        return round(
            vzdalenost_km(
                self.hass.config.latitude, self.hass.config.longitude, u.latitude, u.longitude
            ),
            1,
        )

    def udalost_dict(self, u: Udalost) -> dict[str, Any]:
        data = u.as_dict()
        data["vzdalenost_km"] = self.vzdalenost(u)
        return data

    def event_data(self, u: Udalost) -> dict[str, Any]:
        data = self.udalost_dict(u)
        data["kraj"] = self.ciselniky.kraj
        data["entry_id"] = self.config_entry.entry_id
        return data
