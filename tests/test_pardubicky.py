"""Pardubický kraj – web hzspa.cz (RSS + aktuální výjezdy + detail), na vzorku živých dat."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.hzs_zasahy.const import DOMAIN, EVENT_NOVY_ZASAH, EVENT_ZMENA_STAVU
from custom_components.hzs_zasahy.hzspa import (
    HZSPA_URL,
    MAX_DETAILU,
    parse_aktualni,
    parse_detail,
    parse_rss,
    stav_id,
)

FIX = Path(__file__).parent / "fixtures" / "pardubicky"


def _cti(nazev: str) -> str:
    return (FIX / nazev).read_text(encoding="utf-8")


RSS = _cti("rss.xml")
RSS_PARDUBICE = _cti("rss_pardubice.xml")
AKTUALNI = _cti("aktualni.html")
DETAIL_AKTIVNI = _cti("detail_252734053.html")
DETAIL_UKONCENY = _cti("detail_252732053.html")


def test_parse_rss():
    p = parse_rss(RSS)
    assert len(p) == 6
    assert p[0].id == 252734053
    assert p[0].misto == "Řečany nad Labem - Labětín"
    assert p[0].typ == "Dopravní nehoda"
    assert p[0].popis is None
    # pubDate je v GMT a sedí s "Ohlášená: Dnes v 20:26"
    assert p[0].cas == datetime(2026, 10, 16, 18, 26, 29, tzinfo=timezone.utc)
    assert p[2].popis == "eCall"


def test_parse_aktualni():
    probihajici, ukoncene = parse_aktualni(AKTUALNI)
    assert probihajici == {252734053: "SaP na místě", 252733053: "SaP na místě"}
    assert 252732053 in ukoncene and len(ukoncene) == 4


def test_parse_aktualni_bez_sekci():
    assert parse_aktualni("<html><body>nic</body></html>") == ({}, set())


def test_parse_detail():
    d = parse_detail(DETAIL_AKTIVNI)
    assert d["podtyp"] == "Se zraněním"
    assert d["okres"] == "Pardubice"
    assert d["část obce"] == "Labětín"
    assert d["jednotky"] == "Zdechovice, HS Přelouč"
    assert "popis" not in d  # "nezadán"
    u = parse_detail(DETAIL_UKONCENY)
    assert u["ulice"] == "Zborovská" and u["stav"] == "Ukončená"


@pytest.mark.parametrize(
    ("text", "kod"),
    [("SaP na místě", 420), ("SaP na cestě", 410), ("Lokalizovaná", 430), ("Likvidovaná", 440),
     ("Otevřená", 400), ("Ukončená", 510), (None, 510)],
)
def test_stav_id(text, kod):
    assert stav_id(text) == kod


def _registruj(mock, *, rss=RSS, rss_okres=RSS_PARDUBICE, aktualni=AKTUALNI) -> None:
    mock.clear_requests()
    mock.get(HZSPA_URL + "rss-aktualni-vyjezdy.php?okres=Pardubice", text=rss_okres)
    mock.get(HZSPA_URL + "rss-aktualni-vyjezdy.php", text=rss)
    mock.get(HZSPA_URL + "aktualni-vyjezdy.php", text=aktualni)
    mock.get(HZSPA_URL + "udalost.php?id=252734053", text=DETAIL_AKTIVNI)
    mock.get(HZSPA_URL + "udalost.php?id=252732053", text=DETAIL_UKONCENY)
    mock.get(re.compile(re.escape(HZSPA_URL) + r"udalost\.php\?id=\d+"), text="<html><body></body></html>")


@pytest.fixture
async def pak(hass: HomeAssistant, freezer):
    hass.config.language = "cs"
    await hass.config.async_set_time_zone("Europe/Prague")
    freezer.move_to("2026-10-16 21:00:00+02:00")
    return hass


async def _pridej(hass, okres: str = "0"):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"kraj": "pardubicky"})
    assert result["step_id"] == "filtr", result
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"okres_id": okres, "typy": []}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    return result


def _stav(hass, entry_id: str, klic: str):
    ent = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry_id}_{klic}")
    return hass.states.get(ent)


async def _obnov(hass, entry_id):
    await hass.config_entries.async_get_entry(entry_id).runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_cely_kraj(pak, aioclient_mock):
    hass = pak
    _registruj(aioclient_mock)
    result = await _pridej(hass)
    assert result["title"] == "HZS Pardubický kraj – celý kraj"
    eid = result["result"].entry_id

    assert _stav(hass, eid, "dnes").state == "6"
    assert _stav(hass, eid, "aktivni").state == "2"
    aktivni = _stav(hass, eid, "aktivni").attributes["zasahy"]
    recany = next(z for z in aktivni if z["id"] == 252734053)
    assert recany["nazev"] == "Dopravní nehoda – se zraněním – Řečany nad Labem"
    assert recany["stav"] == "SaP na místě"
    assert recany["okres"] == "Pardubice"
    assert recany["cast_obce"] == "Labětín"
    assert recany["jednotky"] == ["HS Přelouč", "Zdechovice"]
    assert recany["cas"] == "2026-10-16T20:26:29+02:00"

    tyden = _stav(hass, eid, "tyden").attributes["zasahy"]
    usti = next(z for z in tyden if z["id"] == 252732053)
    assert usti["nazev"] == "Planý poplach – Ústí nad Orlicí"
    assert usti["ulice"] == "Zborovská"
    assert usti["poznamka"] == "eCall"
    assert usti["stav"] == "ukončená" and usti["aktivni"] is False
    lanskroun = next(z for z in tyden if z["id"] == 252726053)
    assert lanskroun["poznamka"] == "OA x moto"
    assert lanskroun["obec"] == "Lanškroun"
    assert hass.states.async_entity_ids("geo_location") == []

    # detaily se stahují po dávkách
    detailu = sum(1 for c in aioclient_mock.mock_calls if c[1].path.endswith("udalost.php"))
    assert detailu == min(6, MAX_DETAILU)


async def test_okres_pres_rss(pak, aioclient_mock):
    hass = pak
    _registruj(aioclient_mock)
    result = await _pridej(hass, okres="3606")
    assert result["title"] == "HZS Pardubický kraj – okres Pardubice"
    eid = result["result"].entry_id
    assert any(c[1].query.get("okres") == "Pardubice" for c in aioclient_mock.mock_calls)
    assert _stav(hass, eid, "dnes").state == "1"
    assert _stav(hass, eid, "posledni").attributes["okres"] == "Pardubice"


async def test_eventy(pak, aioclient_mock):
    hass = pak
    _registruj(aioclient_mock)
    result = await _pridej(hass)
    eid = result["result"].entry_id
    nove = async_capture_events(hass, EVENT_NOVY_ZASAH)
    zmeny = async_capture_events(hass, EVENT_ZMENA_STAVU)

    # nový výjezd v RSS i v probíhajících
    novy_rss = RSS.replace(
        "<item>",
        "<item><title><![CDATA[Holice]]></title><link>http://www.hzspa.cz/vyjezdy/udalost.php?id=252740053</link>"
        '<description><![CDATA[<p><img src="x" alt="Požár" /> hoří kůlna</p>]]></description>'
        "<pubDate>Fri, 16 Oct 2026 18:55:00 GMT</pubDate>"
        "<guid>http://www.hzspa.cz/vyjezdy/udalost.php?id=252740053</guid></item><item>",
        1,
    )
    novy_akt = AKTUALNI.replace(
        "<tr><th colspan=\"6\">Probíhající výjezdy</th></tr>",
        "<tr><th colspan=\"6\">Probíhající výjezdy</th></tr>"
        '<tr><td><img alt="Požár" /></td><td>Dnes</td><td>20:55</td>'
        '<td><a href="udalost.php?id=252740053">Holice</a></td><td>Požár</td><td>SaP na cestě</td></tr>',
    )
    _registruj(aioclient_mock, rss=novy_rss, aktualni=novy_akt)
    await _obnov(hass, eid)
    assert len(nove) == 1 and not zmeny
    assert nove[0].data["nazev"] == "Požár – Holice"
    assert nove[0].data["stav"] == "SaP na cestě"
    assert nove[0].data["poznamka"] == "hoří kůlna"
    assert nove[0].data["kraj"] == "Pardubický kraj"

    # Řečany skončily (zmizely z probíhajících), Holice na místě
    akt2 = novy_akt.replace("SaP na cestě", "SaP na místě").replace(
        '<td><a href="udalost.php?id=252734053">Řečany nad Labem - Labětín</a></td><td>Dopravní nehoda</td><td>SaP na místě</td>',
        "",
    )
    _registruj(aioclient_mock, rss=novy_rss, aktualni=akt2)
    await _obnov(hass, eid)
    assert len(nove) == 1
    zmenene = {z.data["id"]: z.data for z in zmeny}
    assert zmenene[252740053]["predchozi_stav"] == "SaP na cestě"
    assert zmenene[252740053]["stav"] == "SaP na místě"
    assert zmenene[252734053]["stav"] == "ukončená"
    assert zmenene[252734053]["aktivni"] is False


async def test_options_bez_techniky_a_mapy(pak, aioclient_mock):
    hass = pak
    _registruj(aioclient_mock)
    result = await _pridej(hass)
    form = await hass.config_entries.options.async_init(result["result"].entry_id)
    assert {str(k) for k in form["data_schema"].schema} == {"typy", "scan_interval"}
