"""Pardubický kraj – vlastní web HZS (www.hzspa.cz/vyjezdy/).

Kraj nemá WebOhled. Web má tři části, ze kterých skládáme stejný model `Udalost`:

- RSS `rss-aktualni-vyjezdy.php` – seznam výjezdů za dnešek a včerejšek s ID,
  přesným časem ohlášení, typem a popisem; umí filtr `?okres=Pardubice`,
- `aktualni-vyjezdy.php` – jediné místo, kde je vidět, které zásahy probíhají a v jakém stavu,
- `udalost.php?id=…` – detail s podtypem, okresem, částí obce, ulicí a jednotkami.

Detail stahujeme jen u nových a probíhajících výjezdů, po malých dávkách.
Souřadnice web nemá.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
import logging
import re
import xml.etree.ElementTree as ET

import aiohttp

from .api import (
    Ciselniky,
    Technika,
    Udalost,
    WebohledConnectionError,
    WebohledError,
    hezky,
)
from .pkr import NEZNAMY_TYP, PKR_TYPY, _TYP_PODLE_NAZVU

_LOGGER = logging.getLogger(__name__)

HZSPA_URL = "https://www.hzspa.cz/vyjezdy/"
HZSPA_KRAJ_ID = 94  # Pardubický v celostátním číselníku

# Okresy – čísla z celostátního číselníku, na webu se filtruje názvem
HZSPA_OKRESY: dict[int, str] = {
    3603: "Chrudim",
    3606: "Pardubice",
    3609: "Svitavy",
    3611: "Ústí nad Orlicí",
}

# Detailů najednou – zbytek se dočte při dalších obnoveních
MAX_DETAILU = 15
DETAIL_SOUBEZNE = 2

STAV_UKONCENA = 510
_ID_RE = re.compile(r"udalost\.php\?id=(\d+)")
_POLE_RE = re.compile(r"<strong>\s*([^<:]+?)\s*:\s*</strong>(.*?)</p>", re.S)


def hzspa_ciselniky() -> Ciselniky:
    return Ciselniky(
        kraj_id=HZSPA_KRAJ_ID,
        kraj="Pardubický kraj",
        typy=dict(PKR_TYPY),
        podtypy={},
        stavy={STAV_UKONCENA: "ukončená"},
        okresy=dict(HZSPA_OKRESY),
        kraje={HZSPA_KRAJ_ID: "Pardubický"},
    )


def stav_id(stav: str | None) -> int:
    """Text stavu z webu -> kód WebOhledu (kvůli eventu změny stavu)."""
    if not stav:
        return STAV_UKONCENA
    s = stav.lower()
    if s.startswith(("ukon", "uzav")):
        return STAV_UKONCENA
    if "cest" in s:
        return 410
    if "míst" in s:
        return 420
    if "lokaliz" in s:
        return 430
    if "likvid" in s:
        return 440
    return 400


@dataclass(frozen=True)
class RssPolozka:
    id: int
    misto: str
    typ: str
    popis: str | None
    cas: datetime


def _text_z_html(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def parse_rss(xml: str) -> list[RssPolozka]:
    try:
        koren = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    except ET.ParseError as err:
        raise WebohledError(f"RSS se nedá přečíst: {err}") from err
    kanal = koren.find("channel")
    if kanal is None:
        raise WebohledError("RSS nemá <channel>")
    out = []
    for item in kanal.findall("item"):
        guid = (item.findtext("guid") or "") + (item.findtext("link") or "")
        m = _ID_RE.search(guid)
        datum = item.findtext("pubDate")
        if not m or not datum:
            continue
        popis_html = item.findtext("description") or ""
        alt = re.search(r'alt="([^"]*)"', popis_html)
        try:
            cas = parsedate_to_datetime(datum).astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        out.append(
            RssPolozka(
                id=int(m.group(1)),
                misto=" ".join((item.findtext("title") or "").split()),
                typ=unescape(alt.group(1)) if alt else "",
                popis=_text_z_html(popis_html) or None,
                cas=cas,
            )
        )
    return out


class _AktualniParser(HTMLParser):
    """Z aktualni-vyjezdy.php vytáhne ID a stav probíhajících výjezdů."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.probihajici: dict[int, str] = {}
        self.ukoncene: set[int] = set()
        self._sekce: str | None = None
        self._v_th = False
        self._th_text: list[str] = []
        self._radek: list[str] | None = None
        self._bunka: list[str] | None = None
        self._id: int | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "th":
            self._v_th, self._th_text = True, []
        elif tag == "tr":
            self._radek, self._id = [], None
        elif tag == "td" and self._radek is not None:
            self._bunka = []
        elif tag == "a" and (m := _ID_RE.search(a.get("href") or "")):
            self._id = int(m.group(1))

    def handle_endtag(self, tag):
        if tag == "th":
            self._v_th = False
            text = "".join(self._th_text).lower()
            if "probíhající" in text:
                self._sekce = "p"
            elif "ukončené" in text:
                self._sekce = "u"
        elif tag == "td" and self._bunka is not None and self._radek is not None:
            self._radek.append(" ".join("".join(self._bunka).split()))
            self._bunka = None
        elif tag == "tr" and self._radek is not None:
            if self._id is not None:
                if self._sekce == "p":
                    self.probihajici[self._id] = self._radek[-1] if len(self._radek) >= 6 else ""
                elif self._sekce == "u":
                    self.ukoncene.add(self._id)
            self._radek = None

    def handle_data(self, data):
        if self._v_th:
            self._th_text.append(data)
        if self._bunka is not None:
            self._bunka.append(data)


def parse_aktualni(html: str) -> tuple[dict[int, str], set[int]]:
    """Vrací ({id: stav} probíhajících, {id} ukončených)."""
    p = _AktualniParser()
    p.feed(html)
    p.close()
    # Bez sekcí (prázdný den nebo změna webu) prostě nic neprobíhá – seznam bereme z RSS
    return p.probihajici, p.ukoncene


def parse_detail(html: str) -> dict[str, str]:
    pole = {}
    for klic, hodnota in _POLE_RE.findall(html):
        text = _text_z_html(hodnota)
        if text and text.lower() not in ("nezadán", "nezadáno", "-"):
            pole[klic.strip().lower()] = text
    return pole


@dataclass
class _Detail:
    pole: dict[str, str] = field(default_factory=dict)
    konecny: bool = False  # stažený až po ukončení – už se nezmění


class HzspaApi:
    """Stejné rozhraní jako WebohledApi (ciselniky, udalosti, technika)."""

    umi_techniku = False  # jednotky dodává detail přímo tady

    def __init__(self, session: aiohttp.ClientSession, user_agent: str, url: str = HZSPA_URL) -> None:
        self._session = session
        self._url = url
        self._headers = {"User-Agent": user_agent, "Accept-Language": "cs"}
        self._detaily: dict[int, _Detail] = {}

    async def _get(self, cesta: str, params: list[tuple[str, str]] | None = None) -> str:
        try:
            async with asyncio.timeout(30):
                resp = await self._session.get(self._url + cesta, params=params, headers=self._headers)
                if resp.status >= 500:
                    raise WebohledConnectionError(f"HTTP {resp.status}")
                if resp.status >= 400:
                    raise WebohledError(f"HTTP {resp.status}")
                return await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise WebohledConnectionError(str(err)) from err

    async def ciselniky(self, kraj_id: int | None = None) -> Ciselniky:
        parse_rss(await self._get("rss-aktualni-vyjezdy.php"))
        return hzspa_ciselniky()

    async def technika(self, udalost_id: int) -> tuple[Technika, ...]:
        return ()

    async def udalosti(
        self, cis: Ciselniky, od: datetime, do: datetime, okres_id: int | None = None
    ) -> list[Udalost]:
        params = [("okres", cis.okresy[okres_id])] if okres_id else None
        rss, aktualni = await asyncio.gather(
            self._get("rss-aktualni-vyjezdy.php", params), self._get("aktualni-vyjezdy.php")
        )
        polozky = parse_rss(rss)
        probihajici, _ = parse_aktualni(aktualni)
        await self._dopln_detaily(polozky, probihajici)
        okres_filtr = cis.okresy.get(okres_id) if okres_id else None
        udalosti = [self._udalost(p, probihajici, okres_filtr) for p in polozky]
        return sorted(udalosti, key=lambda u: u.cas, reverse=True)

    async def _dopln_detaily(self, polozky: list[RssPolozka], probihajici: dict[int, str]) -> None:
        platne = {p.id for p in polozky}
        self._detaily = {k: v for k, v in self._detaily.items() if k in platne}

        def potreba(p: RssPolozka) -> bool:
            d = self._detaily.get(p.id)
            if d is None:
                return True  # ještě nemáme
            if p.id in probihajici:
                return False  # stav a jednotky se mění, ale stav máme z přehledu; jednou stačí
            return not d.konecny  # po ukončení jednou dočíst konečné jednotky

        k_nacteni = sorted((p for p in polozky if potreba(p)), key=lambda p: p.cas, reverse=True)
        k_nacteni = k_nacteni[:MAX_DETAILU]
        sem = asyncio.Semaphore(DETAIL_SOUBEZNE)

        async def nacti(p: RssPolozka) -> None:
            async with sem:
                try:
                    html = await self._get("udalost.php", [("id", str(p.id))])
                except WebohledError as err:
                    _LOGGER.debug("Detail %s se nenačetl: %s", p.id, err)
                    return
            self._detaily[p.id] = _Detail(parse_detail(html), konecny=p.id not in probihajici)

        await asyncio.gather(*(nacti(p) for p in k_nacteni))

    def _udalost(
        self, p: RssPolozka, probihajici: dict[int, str], okres_filtr: str | None
    ) -> Udalost:
        d = self._detaily.get(p.id, _Detail()).pole
        obec, _, cast = p.misto.partition(" - ")
        aktivni = p.id in probihajici
        stav = probihajici.get(p.id) or (d.get("stav") if aktivni else None) or "ukončená"
        typ = d.get("typ") or p.typ
        podtyp = d.get("podtyp") or ""
        jednotky = [j.strip() for j in d.get("jednotky", "").split(",") if j.strip()]
        popis = d.get("popis") or p.popis
        return Udalost(
            id=p.id,
            cas=p.cas,
            typ_id=_TYP_PODLE_NAZVU.get(typ.lower(), NEZNAMY_TYP),
            typ=hezky(typ, velke_prvni=True),
            podtyp_id=None,
            podtyp=hezky(podtyp),
            stav_id=stav_id(stav) if aktivni else STAV_UKONCENA,
            stav=stav,
            okres=d.get("okres") or okres_filtr,
            obec=d.get("obec") or obec.strip(),
            cast_obce=d.get("část obce") or (cast.strip() or None),
            orp=None,
            ulice=d.get("ulice"),
            silnice=None,
            poznamka=popis,
            latitude=None,
            longitude=None,
            technika=tuple(
                Technika(typ="", jednotka=j, pocet=1, aktualni_pocet=1 if aktivni else 0)
                for j in jednotky
            ),
        )
