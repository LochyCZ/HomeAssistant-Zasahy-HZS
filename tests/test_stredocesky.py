"""Středočeský kraj – Portál krizového řízení (HTML), na uložené stránce okresu Rakovník."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.hzs_zasahy.const import DOMAIN, EVENT_NOVY_ZASAH, EVENT_ZMENA_STAVU
from custom_components.hzs_zasahy.pkr import (
    PKR_URL,
    PkrApi,
    ma_dalsi_stranku,
    parse_stranku,
    pkr_ciselniky,
)

HTML = (Path(__file__).parent / "fixtures" / "stredocesky" / "rakovnik.html").read_text(encoding="utf-8")


def _radek(nazev: str, stav: str, misto: str, stupen: str, cas: str) -> str:
    typ = nazev.split(" - ")[0]
    return (
        f'<tr class="row1"><th>{nazev}</th><td><span title="{typ}"><img src="x"></span></td>'
        f'<td>{stav} <img src="y" /></td><td>{misto}</td>'
        f'<td align="center">{stupen}</td><td>{cas}</td></tr>'
    )


POZAR = _radek("požár - rodinný dům - Kněževes", "probíhá zásah", "Kněževes<br>okres Rakovník", "II.", "9. října 2026, 17:30")
POZAR_UKONCEN = _radek("požár - rodinný dům - Kněževes", "ukončená", "Kněževes<br>okres Rakovník", "II.", "9. října 2026, 18:45")
STROM_1 = _radek("technická pomoc - odstranění stromu - Lužná", "probíhá zásah", "Lužná<br>okres Rakovník", "I.", "9. října 2026, 17:35")
STROM_2 = _radek("technická pomoc - odstranění stromu - Lužná", "probíhá zásah", "Lužná<br>okres Rakovník", "I.", "9. října 2026, 17:40")


def _s(*radky: str) -> str:
    return HTML.replace("<tbody>", "<tbody>" + "".join(radky), 1)


def test_parse_stranku():
    r = parse_stranku(HTML)
    assert len(r) == 26
    assert r[0].typ == "únik nebezpečných látek"
    assert r[0].podtyp == "na komunikaci ropa"
    assert r[0].obec == "Rakovník" and r[0].okres is None
    assert r[0].stupen == "I."
    assert r[0].cas == datetime(2026, 10, 9, 15, 5, tzinfo=timezone.utc)
    assert r[1].okres == "Rakovník"


def test_dalsi_stranka():
    assert not ma_dalsi_stranku(HTML, 1)
    assert ma_dalsi_stranku('<a href="?p=2&amp;q=">2</a>', 1)
    # přesný tvar z živého webu při řazení podle aktualizace
    assert ma_dalsi_stranku('<a href="?o=-5&amp;p=2&amp;q=">2</a>', 1)
    assert not ma_dalsi_stranku('<a href="?o=-5&amp;p=12&amp;q=">12</a>', 1)
    assert not ma_dalsi_stranku('<a href="?p=2&amp;q=">2</a>', 2)


def test_identita_a_dva_stejne_zasahy():
    api = PkrApi(None, "test")  # session se při převodu nepoužije
    cis = pkr_ciselniky()
    prvni = api._na_udalosti(parse_stranku(_s(STROM_1, STROM_2, POZAR)), cis)
    assert len(prvni) == 29
    stromy = [u for u in prvni if u.obec == "Lužná" and u.aktivni]
    assert len(stromy) == 2 and stromy[0].id != stromy[1].id
    pozar = next(u for u in prvni if u.obec == "Kněževes")
    assert pozar.typ == "Požár" and pozar.typ_id == 3100 and pozar.stupen == "II."
    # stejná událost s novějším časem a jiným stavem si ponechá ID
    druhe = api._na_udalosti(parse_stranku(_s(STROM_1, STROM_2, POZAR_UKONCEN)), cis)
    pozar2 = next(u for u in druhe if u.obec == "Kněževes")
    assert pozar2.id == pozar.id
    assert not pozar2.aktivni
    assert {u.id for u in druhe if u.obec == "Lužná" and u.aktivni} == {u.id for u in stromy}


def test_okresni_mesto_dostane_okres():
    api = PkrApi(None, "test")
    u = api._na_udalosti(parse_stranku(HTML), pkr_ciselniky())
    assert next(x for x in u if x.obec == "Rakovník").okres == "Rakovník"


@pytest.fixture
async def stc(hass: HomeAssistant, freezer):
    hass.config.language = "cs"
    await hass.config.async_set_time_zone("Europe/Prague")
    hass.config.latitude = 50.1037  # Rakovník
    hass.config.longitude = 13.7335
    freezer.move_to("2026-10-09 18:00:00+02:00")
    return hass


async def _pridej(hass, okres: str = "1192"):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"kraj": "stredocesky"})
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


async def test_okres_rakovnik(stc, aioclient_mock):
    hass = stc
    aioclient_mock.get(PKR_URL, text=HTML)
    result = await _pridej(hass)
    assert result["title"] == "HZS Středočeský kraj – okres Rakovník"
    assert result["options"]["scan_interval"] == 10
    eid = result["result"].entry_id

    url = aioclient_mock.mock_calls[-1][1]
    assert url.query["r_cast_uzemi__in"] == "1192"
    assert url.query["o"] == "-5"

    assert _stav(hass, eid, "tyden").state == "26"
    assert _stav(hass, eid, "dnes").state == "5"
    assert _stav(hass, eid, "aktivni").state == "0"
    posledni = _stav(hass, eid, "posledni")
    assert posledni.state == "Únik nebezpečných látek – na komunikaci ropa – Rakovník"
    assert posledni.attributes["stupen"] == "I."
    assert posledni.attributes["okres"] == "Rakovník"
    assert posledni.attributes["vzdalenost_km"] is None
    assert _stav(hass, eid, "nejblizsi").state == "unknown"
    assert hass.states.async_entity_ids("geo_location") == []

    nove = async_capture_events(hass, EVENT_NOVY_ZASAH)
    zmeny = async_capture_events(hass, EVENT_ZMENA_STAVU)

    aioclient_mock.clear_requests()
    aioclient_mock.get(PKR_URL, text=_s(POZAR))
    await _obnov(hass, eid)
    assert len(nove) == 1 and not zmeny
    assert nove[0].data["nazev"] == "Požár – rodinný dům – Kněževes"
    assert nove[0].data["stupen"] == "II."
    assert nove[0].data["kraj"] == "Středočeský kraj"
    assert _stav(hass, eid, "aktivni").state == "1"

    aioclient_mock.clear_requests()
    aioclient_mock.get(PKR_URL, text=_s(POZAR_UKONCEN))
    await _obnov(hass, eid)
    assert len(nove) == 1
    assert len(zmeny) == 1
    assert zmeny[0].data["predchozi_stav"] == "probíhá zásah"
    assert zmeny[0].data["stav"] == "ukončená"
    assert _stav(hass, eid, "aktivni").state == "0"


async def test_cely_kraj_strankuje(stc, aioclient_mock):
    hass = stc
    s_odkazem = HTML.replace('<p class="paginator">', '<p class="paginator"><a href="?p=2&amp;q=">2</a>', 1)
    aioclient_mock.get(PKR_URL + "?p=2", text=s_odkazem.replace("<tbody>", "<tbody>" + POZAR, 1).replace("?p=2&amp;q=", "?p=3&amp;q="))
    aioclient_mock.get(PKR_URL + "?p=3", text=_s(STROM_1))
    aioclient_mock.get(PKR_URL, text=s_odkazem)
    result = await _pridej(hass, okres="0")
    assert result["title"] == "HZS Středočeský kraj – celý kraj"
    stranky = [c[1].query.get("p") for c in aioclient_mock.mock_calls]
    assert stranky[-3:] == [None, "2", "3"]  # víc než MAX_STRAN (3) nestahujeme
    eid = result["result"].entry_id
    # 26 z každé stránky jsou tytéž řádky -> po sloučení 26 + požár + strom
    assert _stav(hass, eid, "tyden").state == "28"


async def test_options_bez_techniky_a_mapy(stc, aioclient_mock):
    hass = stc
    aioclient_mock.get(PKR_URL, text=HTML)
    result = await _pridej(hass)
    form = await hass.config_entries.options.async_init(result["result"].entry_id)
    klice = {str(k) for k in form["data_schema"].schema}
    assert klice == {"typy", "scan_interval"}


async def test_nedostupny_portal(stc, aioclient_mock):
    hass = stc
    aioclient_mock.get(PKR_URL, status=503, text="Service Unavailable")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"kraj": "stredocesky"})
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}
