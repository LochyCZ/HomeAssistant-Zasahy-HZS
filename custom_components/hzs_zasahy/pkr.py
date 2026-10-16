"""Středočeský kraj – Portál krizového řízení (pkr.kr-stredocesky.cz).

Kraj nemá WebOhled, jen HTML tabulku zásahů. Tenhle modul ji převádí na stejný
model `Udalost` jako ostatní kraje, takže senzory a eventy fungují stejně.

Rozdíly proti WebOhledu:
- žádné ID událostí – identitu skládáme z názvu a obce, viz `PkrApi._prirad_id`,
- žádné souřadnice ani technika, zato je tu stupeň poplachu,
- čas je „poslední aktualizace dat“, ne čas ohlášení.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from html.parser import HTMLParser
import re

import aiohttp

from .api import (
    TZ,
    Ciselniky,
    Technika,
    Udalost,
    WebohledConnectionError,
    WebohledError,
    hezky,
)

PKR_URL = "https://pkr.kr-stredocesky.cz/pkr/zasahy-jpo/"
PKR_KRAJ_ID = 27  # Středočeský v celostátním číselníku

# r_cast_uzemi__in – čísla okresů z filtru na webu (ověřeno)
PKR_OKRESY: dict[int, str] = {
    1181: "Benešov",
    1182: "Beroun",
    1183: "Kladno",
    1184: "Kolín",
    1185: "Kutná Hora",
    1186: "Mělník",
    1187: "Mladá Boleslav",
    1188: "Nymburk",
    1189: "Praha-východ",
    1190: "Praha-západ",
    1191: "Příbram",
    1192: "Rakovník",
}

# Typy událostí – stejná čísla jako v číselníku WebOhledu, ať filtr typů funguje všude
PKR_TYPY: dict[int, str] = {
    3100: "POŽÁR",
    3200: "DOPRAVNÍ NEHODA",
    3400: "ÚNIK NEBEZPEČNÝCH LÁTEK",
    3500: "TECHNICKÁ POMOC",
    3550: "ZÁCHRANA OSOB A ZVÍŘAT",
    3600: "FORMÁLNĚ ZALOŽENÁ UDÁLOST",
    3700: "JINÁ UDÁLOST",
    3800: "PLANÝ POPLACH",
    3900: "TECHNOLOGICKÝ TEST",
    5000: "UDÁLOST NA OBJEKT",
}
_TYP_PODLE_NAZVU = {n.lower(): i for i, n in PKR_TYPY.items()}
NEZNAMY_TYP = 3700

# Stav na webu je jen text; mapujeme na kódy WebOhledu kvůli eventu změny stavu
STAV_PROBIHA = 420
STAV_UKONCENA = 510

# Celý kraj má stovky zásahů týdně po 50 na stránku – víc stránek nestahujeme
MAX_STRAN = 3
# Stejný název a obec do tolika hodin = stejná událost (aktualizace, ne nový výjezd)
AKTUALIZACE_OKNO = timedelta(hours=6)

MESICE = {
    "ledna": 1, "února": 2, "března": 3, "dubna": 4, "května": 5, "června": 6,
    "července": 7, "srpna": 8, "září": 9, "října": 10, "listopadu": 11, "prosince": 12,
    "leden": 1, "únor": 2, "březen": 3, "duben": 4, "květen": 5, "červen": 6,
    "červenec": 7, "srpen": 8, "říjen": 10, "listopad": 11, "prosinec": 12,
}
_DATUM_RE = re.compile(r"(\d{1,2})\.\s*([^\s\d,]+)\s+(\d{4}),?\s*(\d{1,2}):(\d{2})")


def pkr_ciselniky() -> Ciselniky:
    return Ciselniky(
        kraj_id=PKR_KRAJ_ID,
        kraj="Středočeský kraj",
        typy=dict(PKR_TYPY),
        podtypy={},
        stavy={STAV_PROBIHA: "probíhá zásah", STAV_UKONCENA: "ukončená"},
        okresy=dict(PKR_OKRESY),
        kraje={PKR_KRAJ_ID: "Středočeský"},
    )


def parse_datum(text: str) -> datetime:
    """'9. října 2026, 17:05' -> aware datetime (UTC)."""
    m = _DATUM_RE.search(text)
    if not m:
        raise WebohledError(f"Neznámý formát data: {text!r}")
    den, mesic, rok, hod, minuta = m.groups()
    cislo = MESICE.get(mesic.lower())
    if cislo is None:
        raise WebohledError(f"Neznámý měsíc: {mesic!r}")
    return datetime(int(rok), cislo, int(den), int(hod), int(minuta), tzinfo=TZ).astimezone(
        timezone.utc
    )


@dataclass(frozen=True)
class PkrRadek:
    nazev: str
    typ: str
    podtyp: str
    stav: str
    obec: str
    okres: str | None
    stupen: str
    cas: datetime

    @property
    def klic(self) -> str:
        return f"{self.nazev}|{self.obec}"


class _TabulkaParser(HTMLParser):
    """Vytáhne buňky řádků z <table id="result_list"><tbody>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nasel_tabulku = False
        self.radky: list[list[dict]] = []
        self._v_tabulce = False
        self._v_tbody = False
        self._radek: list[dict] | None = None
        self._bunka: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "table" and a.get("id") == "result_list":
            self._v_tabulce = self.nasel_tabulku = True
        elif not self._v_tabulce:
            return
        elif tag == "tbody":
            self._v_tbody = True
        elif self._v_tbody and tag == "tr":
            self._radek = []
        elif self._radek is not None and tag in ("th", "td"):
            self._bunka = {"text": [], "title": None}
        elif self._bunka is not None:
            if tag == "br":
                self._bunka["text"].append("\n")
            elif tag == "span" and a.get("title") and not self._bunka["title"]:
                self._bunka["title"] = a["title"]

    def handle_endtag(self, tag: str) -> None:
        if not self._v_tabulce:
            return
        if tag == "table":
            self._v_tabulce = False
        elif tag == "tbody":
            self._v_tbody = False
        elif tag in ("th", "td") and self._bunka is not None and self._radek is not None:
            self._radek.append(
                {"text": "".join(self._bunka["text"]).strip(), "title": self._bunka["title"]}
            )
            self._bunka = None
        elif tag == "tr" and self._radek is not None:
            if self._radek:
                self.radky.append(self._radek)
            self._radek = None

    def handle_data(self, data: str) -> None:
        if self._bunka is not None:
            self._bunka["text"].append(data)


def parse_stranku(html: str) -> list[PkrRadek]:
    p = _TabulkaParser()
    p.feed(html)
    p.close()
    if not p.nasel_tabulku:
        raise WebohledError("Na stránce není tabulka zásahů (result_list)")
    radky = []
    for bunky in p.radky:
        if len(bunky) < 6:
            continue
        nazev = " ".join(bunky[0]["text"].split())
        casti = [c.strip() for c in nazev.split(" - ")]
        typ = casti[0]
        podtyp = " - ".join(casti[1:-1]) if len(casti) >= 3 else ""
        misto = [r.strip() for r in bunky[3]["text"].split("\n") if r.strip()]
        okres = next(
            (r.removeprefix("okres ").strip() for r in misto[1:] if r.startswith("okres ")), None
        )
        radky.append(
            PkrRadek(
                nazev=nazev,
                typ=bunky[1]["title"] or typ,
                podtyp=podtyp,
                stav=" ".join(bunky[2]["text"].split()),
                obec=misto[0] if misto else "",
                okres=okres,
                stupen=bunky[4]["text"],
                cas=parse_datum(bunky[5]["text"]),
            )
        )
    return radky


def ma_dalsi_stranku(html: str, strana: int) -> bool:
    return re.search(rf"[?&;]p={strana + 1}(?:&|\")", html) is not None


def _stav_id(stav: str) -> int:
    return STAV_UKONCENA if stav.lower().startswith("ukon") else STAV_PROBIHA


def _nove_id(klic: str, cas: datetime) -> int:
    return int(sha1(f"{klic}|{cas.isoformat()}".encode()).hexdigest()[:12], 16)


class PkrApi:
    """Stejné rozhraní jako WebohledApi (ciselniky, udalosti, technika)."""

    umi_techniku = False

    def __init__(self, session: aiohttp.ClientSession, user_agent: str, url: str = PKR_URL) -> None:
        self._session = session
        self._url = url
        self._headers = {"User-Agent": user_agent, "Accept-Language": "cs"}
        # klíč (název|obec) -> [(id, čas prvního výskytu, čas poslední aktualizace)]
        self._identity: dict[str, list[tuple[int, datetime, datetime]]] = {}

    async def _stranka(self, params: list[tuple[str, str]]) -> str:
        try:
            async with asyncio.timeout(30):
                resp = await self._session.get(self._url, params=params, headers=self._headers)
                if resp.status >= 500:
                    raise WebohledConnectionError(f"HTTP {resp.status}")
                if resp.status >= 400:
                    raise WebohledError(f"HTTP {resp.status}")
                return await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise WebohledConnectionError(str(err)) from err

    async def ciselniky(self, kraj_id: int | None = None) -> Ciselniky:
        # Ověří, že web odpovídá a má tabulku; číselníky jsou pevné
        parse_stranku(await self._stranka([("q", ""), ("o", "-5"), ("r_cast_uzemi__in", "1192")]))
        return pkr_ciselniky()

    async def udalosti(
        self, cis: Ciselniky, od: datetime, do: datetime, okres_id: int | None = None
    ) -> list[Udalost]:
        zaklad = [("q", ""), ("o", "-5")]  # řazení podle poslední aktualizace, nejnovější první
        if okres_id:
            zaklad.append(("r_cast_uzemi__in", str(okres_id)))
        radky: list[PkrRadek] = []
        for strana in range(1, MAX_STRAN + 1):
            params = zaklad + ([("p", str(strana))] if strana > 1 else [])
            html = await self._stranka(params)
            radky += parse_stranku(html)
            if not ma_dalsi_stranku(html, strana):
                break
        return self._na_udalosti(radky, cis)

    async def technika(self, udalost_id: int) -> tuple[Technika, ...]:
        return ()

    def _prirad_id(self, r: PkrRadek, pouzite: set[int]) -> int:
        """Stejný název a obec s novějším časem do 6 h = stejná událost (změna stavu).

        Dva různé zásahy se stejným názvem v jednom výpisu (třeba víc spadlých
        stromů v jedné obci) nesmí dostat stejné ID – proto `pouzite`.
        """
        zaznamy = self._identity.setdefault(r.klic, [])
        for i, (uid, prvni, posledni) in enumerate(zaznamy):
            if uid in pouzite:
                continue
            if prvni <= r.cas and r.cas - posledni <= AKTUALIZACE_OKNO:
                zaznamy[i] = (uid, prvni, max(posledni, r.cas))
                return uid
        uid = _nove_id(r.klic, r.cas)
        zaznamy.append((uid, r.cas, r.cas))
        return uid

    def _na_udalosti(self, radky: list[PkrRadek], cis: Ciselniky) -> list[Udalost]:
        # nejstarší první, ať se identita přiřazuje ve správném pořadí
        udalosti: dict[int, Udalost] = {}
        pouzite: set[int] = set()
        for r in sorted(radky, key=lambda x: x.cas):
            uid = self._prirad_id(r, pouzite)
            if uid in pouzite:  # stejný název, obec i čas – opravdu duplicitní řádek
                continue
            pouzite.add(uid)
            typ_id = _TYP_PODLE_NAZVU.get(r.typ.lower(), NEZNAMY_TYP)
            stav_id = _stav_id(r.stav)
            udalosti[uid] = Udalost(
                id=uid,
                cas=r.cas,
                typ_id=typ_id,
                typ=hezky(r.typ, velke_prvni=True),
                podtyp_id=None,
                podtyp=r.podtyp,
                stav_id=stav_id,
                stav=r.stav,
                okres=r.okres or self._okres_z_filtru(cis, r),
                obec=r.obec,
                cast_obce=None,
                orp=None,
                ulice=None,
                silnice=None,
                poznamka=None,
                latitude=None,
                longitude=None,
                stupen=r.stupen or None,
            )
        # zapomenout identity starší než týden
        hranice = datetime.now(timezone.utc) - timedelta(days=10)
        for klic in list(self._identity):
            self._identity[klic] = [z for z in self._identity[klic] if z[2] >= hranice]
            if not self._identity[klic]:
                del self._identity[klic]
        return sorted(udalosti.values(), key=lambda u: u.cas, reverse=True)

    @staticmethod
    def _okres_z_filtru(cis: Ciselniky, r: PkrRadek) -> str | None:
        # okresní města web vypisuje bez řádku „okres …“ – obec = název okresu
        for nazev in cis.okresy.values():
            if nazev == r.obec:
                return nazev
        return None
