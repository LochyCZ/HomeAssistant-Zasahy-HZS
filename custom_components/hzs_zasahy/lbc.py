"""Liberecký kraj – dopravní nehody z Portálu krizového řízení (pkr.kraj-lbc.cz).

HZS Libereckého kraje výjezdy veřejně nezveřejňuje. Portál kraje má jen stránku
„Dopravní informace“ (dopravní zpravodajství: nehody, uzavírky, překážky).
Z ní bereme jen dopravní nehody (typ 124), požáry ani jiné zásahy tu nejsou.

Data skládáme ze dvou dotazů na stejnou stránku:
- HTML tabulka – typ, popis, obec, ORP, čas vzniku a předpokládaného ukončení,
- `?fmt=json` – souřadnice (S-JTSK se záporným znaménkem) podle ID.
Každá událost má stálé ID. Probíhající je, dokud nenastal čas ukončení.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
import re

import aiohttp

from .api import (
    Ciselniky,
    Technika,
    Udalost,
    WebohledConnectionError,
    WebohledError,
)
from .krovak import krovak_to_wgs84
from .pkr import parse_datum

LBC_URL = "https://pkr.kraj-lbc.cz/pkr/probihajici-udalosti/"
LBC_KRAJ_ID = 78  # Liberecký v celostátním číselníku
LBC_TYP_NEHODA = 124  # typ „dopravní nehoda“ ve filtru portálu
TYP_DOPRAVNI_NEHODA = 3200  # stejné číslo jako ve WebOhledu

# Okresy – čísla z celostátního číselníku; portál uvádí jen ORP
LBC_OKRESY: dict[int, str] = {
    3501: "Česká Lípa",
    3504: "Jablonec nad Nisou",
    3505: "Liberec",
    3608: "Semily",
}
ORP_OKRES: dict[str, str] = {
    "Česká Lípa": "Česká Lípa",
    "Nový Bor": "Česká Lípa",
    "Jablonec nad Nisou": "Jablonec nad Nisou",
    "Tanvald": "Jablonec nad Nisou",
    "Železný Brod": "Jablonec nad Nisou",
    "Liberec": "Liberec",
    "Frýdlant": "Liberec",
    "Semily": "Semily",
    "Jilemnice": "Semily",
    "Turnov": "Semily",
}

STAV_PROBIHA = 420
STAV_UKONCENA = 510
MAX_STRAN = 3

_RADEK_RE = re.compile(r"<tr class=\"row\d\">(.*?)</tr>", re.S)
_BUNKA_RE = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S)
_ID_RE = re.compile(r"probihajici-udalosti/(\d+)/")
_DATUM_SPAN_RE = re.compile(
    r'responsive_date">([^<]+)</span>\s*<span class="responsive_time">(\d{1,2}:\d{2})'
)
_ULICE_RE = re.compile(r"v ulici (.+?) v obci", re.I)
_SILNICE_RE = re.compile(r"na silnici ([\w./-]+)", re.I)
_OD_DO_RE = re.compile(r"^Od [^;]*;\s*")


def lbc_ciselniky() -> Ciselniky:
    return Ciselniky(
        kraj_id=LBC_KRAJ_ID,
        kraj="Liberecký kraj (dopravní nehody)",
        typy={TYP_DOPRAVNI_NEHODA: "DOPRAVNÍ NEHODA"},
        podtypy={},
        stavy={STAV_PROBIHA: "probíhá", STAV_UKONCENA: "ukončená"},
        okresy=dict(LBC_OKRESY),
        kraje={LBC_KRAJ_ID: "Liberecký"},
    )


def _text(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


@dataclass(frozen=True)
class LbcRadek:
    id: int
    nazev: str
    popis: str
    obec: str
    orp: str | None
    vznik: datetime
    ukonceni: datetime | None


def _datum(bunka: str) -> datetime | None:
    m = _DATUM_SPAN_RE.search(bunka)
    if not m:
        return None
    return parse_datum(f"{m.group(1).strip()}, {m.group(2)}")


def parse_tabulku(html: str) -> list[LbcRadek]:
    if 'id="result_list"' not in html:
        raise WebohledError("Na stránce není tabulka událostí (result_list)")
    radky = []
    for radek in _RADEK_RE.findall(html):
        bunky = _BUNKA_RE.findall(radek)
        m = _ID_RE.search(radek)
        if len(bunky) < 6 or not m:
            continue
        vznik = _datum(bunky[4])
        if vznik is None:
            continue
        lokace = [_text(c) for c in re.split(r"<br\s*/?>", bunky[3])]
        obec = next((x.removeprefix("obec:").strip() for x in lokace if x.startswith("obec:")), "")
        orp = next((x.removeprefix("ORP:").strip() for x in lokace if x.startswith("ORP:")), None)
        radky.append(
            LbcRadek(
                id=int(m.group(1)),
                nazev=_text(bunky[0]),
                popis=_text(bunky[2]),
                obec=obec,
                orp=orp,
                vznik=vznik,
                ukonceni=_datum(bunky[5]),
            )
        )
    return radky


def parse_souradnice(data: object) -> dict[int, tuple[float, float]]:
    """{id: (lat, lon)} z ?fmt=json."""
    if not isinstance(data, dict) or "result_items" not in data:
        raise WebohledError("JSON portálu nemá result_items")
    out: dict[int, tuple[float, float]] = {}
    for skupina in data["result_items"] or []:
        for p in (skupina or {}).get("ret") or []:
            try:
                y = abs(float(p["geom"]["lon"]))
                x = abs(float(p["geom"]["lat"]))
                uid = int(p["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if 400_000 < y < 950_000 and 930_000 < x < 1_250_000:
                lat, lon = krovak_to_wgs84(y, x)
                out[uid] = (round(lat, 6), round(lon, 6))
    return out


def ma_dalsi_stranku(html: str, strana: int) -> bool:
    return re.search(rf"[?&;]p={strana + 1}(?:&|\")", html) is not None


def na_udalosti(
    radky: list[LbcRadek],
    souradnice: dict[int, tuple[float, float]],
    okres_filtr: str | None,
    ted: datetime,
) -> list[Udalost]:
    udalosti: dict[int, Udalost] = {}
    for r in radky:
        okres = ORP_OKRES.get(r.orp or "")
        if okres_filtr and okres != okres_filtr:
            continue
        aktivni = r.ukonceni is None or ted < r.ukonceni
        lat, lon = souradnice.get(r.id, (None, None))
        ulice = _ULICE_RE.search(r.nazev)
        silnice = _SILNICE_RE.search(r.nazev)
        udalosti[r.id] = Udalost(
            id=r.id,
            cas=r.vznik,
            typ_id=TYP_DOPRAVNI_NEHODA,
            typ="Dopravní nehoda",
            podtyp_id=None,
            podtyp="",
            stav_id=STAV_PROBIHA if aktivni else STAV_UKONCENA,
            stav="probíhá" if aktivni else "ukončená",
            okres=okres,
            obec=r.obec,
            cast_obce=None,
            orp=r.orp,
            ulice=ulice.group(1).strip() if ulice else None,
            silnice=silnice.group(1) if silnice else None,
            poznamka=_OD_DO_RE.sub("", r.popis) or None,
            latitude=lat,
            longitude=lon,
        )
    return sorted(udalosti.values(), key=lambda u: u.cas, reverse=True)


class LbcApi:
    """Stejné rozhraní jako WebohledApi (ciselniky, udalosti, technika)."""

    umi_techniku = False

    def __init__(self, session: aiohttp.ClientSession, user_agent: str, url: str = LBC_URL) -> None:
        self._session = session
        self._url = url
        self._headers = {"User-Agent": user_agent, "Accept-Language": "cs"}

    async def _get(self, params: list[tuple[str, str]], json: bool = False) -> object:
        try:
            async with asyncio.timeout(30):
                resp = await self._session.get(self._url, params=params, headers=self._headers)
                if resp.status >= 500:
                    raise WebohledConnectionError(f"HTTP {resp.status}")
                if resp.status >= 400:
                    raise WebohledError(f"HTTP {resp.status}")
                if json:
                    return await resp.json(content_type=None)
                return await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise WebohledConnectionError(str(err)) from err
        except ValueError as err:
            raise WebohledError(f"Odpověď není JSON: {err}") from err

    async def ciselniky(self, kraj_id: int | None = None) -> Ciselniky:
        parse_tabulku(await self._get([("typ__id__in", str(LBC_TYP_NEHODA))]))
        return lbc_ciselniky()

    async def technika(self, udalost_id: int) -> tuple[Technika, ...]:
        return ()

    async def udalosti(
        self, cis: Ciselniky, od: datetime, do: datetime, okres_id: int | None = None
    ) -> list[Udalost]:
        zaklad = [("typ__id__in", str(LBC_TYP_NEHODA))]
        radky: list[LbcRadek] = []
        souradnice: dict[int, tuple[float, float]] = {}
        for strana in range(1, MAX_STRAN + 1):
            strankovani = [("p", str(strana))] if strana > 1 else []
            html, data = await asyncio.gather(
                self._get(zaklad + strankovani),
                self._get(zaklad + strankovani + [("fmt", "json")], json=True),
            )
            radky += parse_tabulku(html)
            souradnice |= parse_souradnice(data)
            if not ma_dalsi_stranku(html, strana):
                break
        okres_filtr = cis.okresy.get(okres_id) if okres_id else None
        return na_udalosti(radky, souradnice, okres_filtr, datetime.now(timezone.utc))
