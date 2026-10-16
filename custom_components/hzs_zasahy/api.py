"""Klient pro JSON API WebOhledu (Přehled událostí HZS) – nezávislý na HA.

Stejný systém provozuje víc krajů (Vysočina, Ústecký…); liší se jen adresou.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import re
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import aiohttp

from .krovak import krovak_to_wgs84

TZ = ZoneInfo("Europe/Prague")

# Stavy, které web posílá pro filtr „Všechny události“ (+ odjezd poslední jednotky)
STAV_IDS_DOTAZ = (
    210, 400, 410, 420, 430, 440, 450, 500, 510, 520,
    600, 610, 620, 700, 710, 750, 760, 780, 800,
)
# Stavy, kdy se na místě ještě zasahuje
AKTIVNI_STAVY = frozenset({200, 210, 300, 400, 410, 420, 430, 440, 450})

_ZKRATKY = ("AED", "EPS", "LDN", "SAP", "OS", "ZPP", "VZ", "SSU", "ZOZ")
_ZKRATKY_RE = re.compile(r"\b(" + "|".join(_ZKRATKY) + r")\b", re.IGNORECASE)


class WebohledError(Exception):
    """API vrátilo chybu nebo nečekaná data."""


class WebohledConnectionError(WebohledError):
    """Server není dostupný."""


def hezky(text: str | None, velke_prvni: bool = False) -> str:
    """'POLNÍ POROST, TRÁVA' -> 'polní porost, tráva' (zkratky zůstanou velké)."""
    if not text:
        return ""
    out = text.strip().lower()
    out = _ZKRATKY_RE.sub(lambda m: "SaP" if m.group(1).lower() == "sap" else m.group(1).upper(), out)
    if velke_prvni and out:
        out = out[0].upper() + out[1:]
    return out


def nazev_kraje(nazev: str) -> str:
    """'Ústecký' -> 'Ústecký kraj', 'Vysočina' a 'Hlavní město Praha' beze změny."""
    nazev = nazev.strip()
    return f"{nazev} kraj" if nazev.endswith("ý") else nazev


@dataclass(frozen=True)
class Ciselniky:
    kraj_id: int | None  # None = web neřekl, který kraj je jeho (chybí /konfigurace)
    kraj: str
    typy: dict[int, str]
    podtypy: dict[int, str]
    stavy: dict[int, str]
    okresy: dict[int, str]  # jen okresy vybraného kraje
    kraje: dict[int, str]  # všechny kraje z číselníku (včetně sousedních)
    vsechny_okresy: tuple[tuple[int, str, int | None], ...] = ()

    @classmethod
    def z_api(cls, data: dict[str, Any], kraj_id: int | None = None) -> Ciselniky:
        """kraj_id má přednost před /konfigurace (starší weby ji nemají)."""
        try:
            konf = data.get("konfigurace") or {}
            if kraj_id is None and konf.get("krajId") is not None:
                kraj_id = int(konf["krajId"])
            cis = cls(
                kraj_id=None,
                kraj="",
                typy={int(t["id"]): t["nazev"] for t in data["typy"]},
                podtypy={int(t["id"]): t["nazev"] for t in data["podtypy"]},
                stavy={int(t["id"]): t["nazev"] for t in data["stavy"]},
                okresy={},
                kraje={int(k["id"]): k["nazev"] for k in data["kraje"]},
                vsechny_okresy=tuple(
                    (
                        int(o["id"]),
                        o["nazev"],
                        int(o["krajId"]) if o.get("krajId") is not None else None,
                    )
                    for o in data["okresy"]
                ),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            raise WebohledError(f"Neočekávaný formát číselníků: {err}") from err
        if not cis.typy or not cis.stavy:
            raise WebohledError("Prázdné číselníky")
        return cis.pro_kraj(kraj_id) if kraj_id is not None else cis

    def pro_kraj(self, kraj_id: int) -> Ciselniky:
        return replace(
            self,
            kraj_id=kraj_id,
            kraj=nazev_kraje(self.kraje.get(kraj_id, str(kraj_id))),
            okresy={i: n for i, n, k in self.vsechny_okresy if k in (kraj_id, None)},
        )


@dataclass(frozen=True)
class Technika:
    typ: str
    jednotka: str
    pocet: int
    aktualni_pocet: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "typ": self.typ,
            "jednotka": self.jednotka,
            "pocet": self.pocet,
            "aktualni_pocet": self.aktualni_pocet,
        }


@dataclass(frozen=True)
class Udalost:
    id: int
    cas: datetime
    typ_id: int
    typ: str
    podtyp_id: int | None
    podtyp: str
    stav_id: int
    stav: str
    okres: str | None
    obec: str
    cast_obce: str | None
    orp: str | None
    ulice: str | None
    silnice: str | None
    poznamka: str | None
    latitude: float | None
    longitude: float | None
    technika: tuple[Technika, ...] = field(default=(), compare=False)
    stupen: str | None = None  # stupeň poplachu (jen Středočeský kraj)

    @property
    def aktivni(self) -> bool:
        return self.stav_id in AKTIVNI_STAVY

    @property
    def nazev(self) -> str:
        casti = [self.typ]
        if self.podtyp and self.podtyp.lower() != self.typ.lower():
            casti.append(self.podtyp)
        casti.append(self.obec)
        return " – ".join(c for c in casti if c)

    @property
    def misto(self) -> str:
        casti = [self.obec]
        if self.cast_obce and self.cast_obce != self.obec:
            casti.append(self.cast_obce)
        if self.ulice:
            casti.append(self.ulice)
        return ", ".join(casti)

    @property
    def jednotky(self) -> list[str]:
        return sorted({t.jednotka for t in self.technika if t.jednotka})

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nazev": self.nazev,
            "typ": self.typ,
            "podtyp": self.podtyp,
            "stav": self.stav,
            "stav_id": self.stav_id,
            "aktivni": self.aktivni,
            "okres": self.okres,
            "obec": self.obec,
            "cast_obce": self.cast_obce,
            "ulice": self.ulice,
            "silnice": self.silnice,
            "orp": self.orp,
            "misto": self.misto,
            "poznamka": self.poznamka,
            "cas": self.cas.astimezone(TZ).isoformat(),
            "latitude": self.latitude,
            "longitude": self.longitude,
            "jednotky": self.jednotky,
            "technika": [t.as_dict() for t in self.technika],
            "stupen": self.stupen,
        }


def _text(value: Any) -> str | None:
    """Ořízne text a zahodí prázdné hodnoty a výplň typu '.', '...', '0'."""
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text or not any(ch.isalnum() for ch in text) or text in ("0", "nezjištěno"):
        return None
    return text


def _cas(text: str) -> datetime:
    """Vysočina posílá UTC se 'Z', Ústecký kraj místní čas bez zóny."""
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(timezone.utc)


def _souradnice(raw: dict[str, Any]) -> tuple[float | None, float | None]:
    try:
        y, x = float(raw["gis1"]), float(raw["gis2"])
    except (KeyError, TypeError, ValueError):
        return None, None
    if not (400_000 < y < 950_000 and 930_000 < x < 1_250_000):
        return None, None
    lat, lon = krovak_to_wgs84(y, x)
    return round(lat, 6), round(lon, 6)


def parse_udalost(raw: dict[str, Any], cis: Ciselniky) -> Udalost:
    try:
        typ_id = int(raw["typId"])
        podtyp_id = int(raw["podtypId"]) if raw.get("podtypId") is not None else None
        stav_id = int(raw["stavId"])
        lat, lon = _souradnice(raw)
        return Udalost(
            id=int(raw["id"]),
            cas=_cas(raw["casOhlaseni"]),
            typ_id=typ_id,
            typ=hezky(cis.typy.get(typ_id, str(typ_id)), velke_prvni=True),
            podtyp_id=podtyp_id,
            podtyp=hezky(cis.podtypy.get(podtyp_id)) if podtyp_id else "",
            stav_id=stav_id,
            stav=cis.stavy.get(stav_id, str(stav_id)),
            okres=(raw.get("okres") or {}).get("nazev"),
            obec=_text(raw.get("obec")) or "",
            cast_obce=_text(raw.get("castObce")),
            orp=_text(raw.get("ORP")),
            ulice=_text(raw.get("ulice")),
            silnice=_text(raw.get("silnice")),
            poznamka=_text(raw.get("poznamkaProMedia")),
            latitude=lat,
            longitude=lon,
        )
    except (KeyError, TypeError, ValueError) as err:
        raise WebohledError(f"Neočekávaný formát události: {err}") from err


def parse_udalosti(data: Any, cis: Ciselniky) -> list[Udalost]:
    if not isinstance(data, list):
        raise WebohledError("API nevrátilo seznam událostí")
    udalosti = [parse_udalost(raw, cis) for raw in data]
    udalosti.sort(key=lambda u: u.cas, reverse=True)
    return udalosti


def parse_techniku(data: Any) -> tuple[Technika, ...]:
    if not isinstance(data, list):
        return ()
    out = []
    for t in data:
        try:
            out.append(
                Technika(
                    typ=str(t.get("typ") or ""),
                    jednotka=str(t.get("jednotka") or ""),
                    pocet=int(t.get("pocet") or 0),
                    aktualni_pocet=int(t.get("aktualniPocet") or 0),
                )
            )
        except (TypeError, ValueError, AttributeError):
            continue
    return tuple(out)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class WebohledApi:
    """Tenký klient nad API WebOhledu (např. https://udalosti.hzsulk.cz/api/)."""

    umi_techniku = True

    def __init__(self, session: aiohttp.ClientSession, api_url: str, user_agent: str) -> None:
        self._session = session
        self.api_url = api_url.rstrip("/") + "/"
        self._api = self.api_url
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}

    async def _get(self, path: str, params: list[tuple[str, str]] | None = None) -> Any:
        try:
            async with asyncio.timeout(30):
                resp = await self._session.get(self._api + path, params=params, headers=self._headers)
                if resp.status >= 400:
                    text = await resp.text()
                    raise WebohledError(f"HTTP {resp.status}: {text[:200]}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise WebohledConnectionError(str(err)) from err
        except ValueError as err:
            raise WebohledError(f"Odpověď není JSON: {err}") from err

    async def _konfigurace(self) -> dict[str, Any] | None:
        """Starší instance (např. Moravskoslezský kraj) endpoint /konfigurace nemají."""
        try:
            konf = await self._get("konfigurace")
        except WebohledConnectionError:
            raise
        except WebohledError:
            return None
        return konf if isinstance(konf, dict) else None

    async def ciselniky(self, kraj_id: int | None = None) -> Ciselniky:
        klice = ("typy", "podtypy", "stavy", "kraje", "okresy")
        vysledky = await asyncio.gather(*(self._get(k) for k in klice), self._konfigurace())
        data = dict(zip((*klice, "konfigurace"), vysledky))
        return Ciselniky.z_api(data, kraj_id)

    async def udalosti(
        self,
        cis: Ciselniky,
        od: datetime,
        do: datetime,
        okres_id: int | None = None,
    ) -> list[Udalost]:
        params = [("casOd", _iso(od)), ("casDo", _iso(do)), ("krajId", str(cis.kraj_id))]
        if okres_id:
            params.append(("okresId", str(okres_id)))
        params += [("stavIds", str(s)) for s in STAV_IDS_DOTAZ]
        return parse_udalosti(await self._get("", params), cis)

    async def technika(self, udalost_id: int) -> tuple[Technika, ...]:
        return parse_techniku(await self._get(f"udalosti/{udalost_id}/technika"))


def kandidati_api(web_url: str) -> list[str]:
    """Kde hledat API k dané adrese webu.

    Většinou je hned pod webem ('/udalosti/' -> '/udalosti/api/'), ale třeba
    Zlínský kraj má web na '/udalosti/' a API na kořeni '/api/'.
    """
    url = web_url.strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    cesta = parts.path.rstrip("/")
    if cesta.endswith("/api"):
        cesta = cesta[: -len("/api")]
    puvod = f"{parts.scheme}://{parts.netloc}"
    kandidati = [f"{puvod}{cesta}/api/", f"{puvod}/api/"]
    return list(dict.fromkeys(kandidati))


async def najdi_api(
    session: aiohttp.ClientSession, web_url: str, user_agent: str
) -> WebohledApi:
    """Vrátí klienta pro první adresu, kde odpovídá API s číselníkem typů."""
    chyby: list[WebohledError] = []
    for kandidat in kandidati_api(web_url):
        api = WebohledApi(session, kandidat, user_agent)
        try:
            typy = await api._get("typy")
        except WebohledError as err:
            chyby.append(err)
            continue
        if isinstance(typy, list) and typy and isinstance(typy[0], dict) and "id" in typy[0]:
            return api
        chyby.append(WebohledError(f"{kandidat}typy nevrací číselník"))
    # Server neodpověděl vůbec -> chyba spojení, jinak "tohle není API"
    if chyby and all(isinstance(e, WebohledConnectionError) for e in chyby):
        raise chyby[0]
    raise WebohledError("; ".join(str(e) for e in chyby) or "API nenalezeno")
