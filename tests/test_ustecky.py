"""Ústecký kraj – stejný systém, jiná adresa a čas bez časové zóny."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import re

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.hzs_zasahy.api import Ciselniky, parse_udalosti
from custom_components.hzs_zasahy.const import DOMAIN, KRAJE

FIX = Path(__file__).parent / "fixtures"
CIS = json.loads((FIX / "ustecky" / "ciselniky.json").read_text(encoding="utf-8"))
UDALOSTI = json.loads((FIX / "ustecky" / "udalosti.json").read_text(encoding="utf-8"))
TECHNIKA = json.loads((FIX / "ustecky" / "technika.json").read_text(encoding="utf-8"))
URL_ULK = KRAJE["ustecky"][1]
API_ULK = URL_ULK + "api/"

CIS_VYS = json.loads((FIX / "vysocina" / "ciselniky.json").read_text(encoding="utf-8"))
UDALOSTI_VYS = json.loads((FIX / "vysocina" / "udalosti.json").read_text(encoding="utf-8"))
API_VYS = KRAJE["vysocina"][1] + "api/"


def test_ciselniky_ustecky():
    cis = Ciselniky.z_api(CIS)
    assert cis.kraj_id == 60
    assert cis.kraj == "Ústecký kraj"
    # číselník obsahuje i okresy jiných krajů – nabízíme jen ústecké
    assert sorted(cis.okresy.values()) == [
        "Chomutov", "Děčín", "Litoměřice", "Louny", "Most", "Teplice", "Ústí nad Labem"
    ]


def test_cas_bez_zony_je_mistni():
    cis = Ciselniky.z_api(CIS)
    u = next(x for x in parse_udalosti(UDALOSTI, cis) if x.id == 356494042)
    # API vrací "2026-10-15T22:05:33" = místní čas (web ukazuje 22:05)
    assert u.cas == datetime(2026, 10, 15, 20, 5, 33, tzinfo=timezone.utc)
    assert u.as_dict()["cas"] == "2026-10-15T22:05:33+02:00"


def test_cas_bez_zony_v_zime():
    cis = Ciselniky.z_api(CIS)
    raw = dict(UDALOSTI[0], casOhlaseni="2026-12-24T18:00:00")
    u = parse_udalosti([raw], cis)[0]
    assert u.cas == datetime(2026, 12, 24, 17, 0, tzinfo=timezone.utc)


def test_udalost_ustecky():
    cis = Ciselniky.z_api(CIS)
    u = parse_udalosti(UDALOSTI, cis)
    assert [x.id for x in u] == sorted([x["id"] for x in UDALOSTI], reverse=True)
    litvinov = u[0]
    assert litvinov.nazev == "Záchrana osob a zvířat – uzavřené prostory, výtah – Litvínov"
    assert litvinov.misto == "Litvínov, Hamr, Přátelství"
    # ověřeno proti pyproj (EPSG:5514 -> EPSG:4326)
    assert litvinov.latitude == pytest.approx(50.59274, abs=1e-4)
    assert litvinov.longitude == pytest.approx(13.57331, abs=1e-4)
    prevzata = next(x for x in u if x.stav_id == 600)
    assert prevzata.stav == "Převzatá VZ"
    assert not prevzata.aktivni
    d7 = next(x for x in u if x.id == 356442042)
    assert d7.silnice == "D7"


def _registruj(mock, api: str, cis: dict, udalosti: list, technika: list) -> None:
    for k in ("typy", "podtypy", "stavy", "kraje", "okresy", "konfigurace"):
        mock.get(api + k, json=cis[k])
    mock.get(re.compile(re.escape(api) + r"udalosti/\d+/technika"), json=technika)
    mock.get(api, json=udalosti)


@pytest.fixture
async def ulk(hass: HomeAssistant, freezer):
    hass.config.language = "cs"
    await hass.config.async_set_time_zone("Europe/Prague")
    hass.config.latitude = 50.6607  # Ústí nad Labem
    hass.config.longitude = 14.0323
    freezer.move_to("2026-10-15 23:00:00+02:00")
    return hass


async def _pridej(hass, kraj: str, okres: str = "0", url: str | None = None):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"kraj": kraj})
    if url:
        assert result["step_id"] == "url"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"base_url": url})
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


async def test_ustecky_okres_most(ulk, aioclient_mock):
    hass = ulk
    _registruj(aioclient_mock, API_ULK, CIS, UDALOSTI, TECHNIKA)
    result = await _pridej(hass, "ustecky", okres="3508")
    assert result["title"] == "HZS Ústecký kraj – okres Most"
    assert result["data"]["base_url"] == "https://udalosti.hzsulk.cz/"
    eid = result["result"].entry_id

    url = next(c[1] for c in aioclient_mock.mock_calls if c[1].path == "/api/")
    assert url.host == "udalosti.hzsulk.cz"
    assert url.query["krajId"] == "60"
    assert url.query["okresId"] == "3508"

    # 15. 10. podle místního času: 4 zásahy (ten z 14. 10. 23:07 už ne)
    assert _stav(hass, eid, "dnes").state == "4"
    assert _stav(hass, eid, "posledni_cas").state == "2026-10-15T20:05:33+00:00"
    posledni = _stav(hass, eid, "posledni")
    assert posledni.attributes["obec"] == "Litvínov"
    assert posledni.attributes["jednotky"] == ["HS Litvínov"]
    assert posledni.attributes["vzdalenost_km"] == pytest.approx(31, abs=3)
    assert _stav(hass, eid, "aktivni").state == "0"


async def test_vlastni_adresa(ulk, aioclient_mock):
    hass = ulk
    _registruj(aioclient_mock, API_ULK, CIS, UDALOSTI, TECHNIKA)
    result = await _pridej(hass, "vlastni", url="https://udalosti.hzsulk.cz")
    assert result["title"] == "HZS Ústecký kraj – celý kraj"
    assert result["data"]["base_url"] == "https://udalosti.hzsulk.cz/"


async def test_dva_kraje_najednou(ulk, aioclient_mock):
    hass = ulk
    _registruj(aioclient_mock, API_ULK, CIS, UDALOSTI, TECHNIKA)
    _registruj(aioclient_mock, API_VYS, CIS_VYS, UDALOSTI_VYS, TECHNIKA)
    r1 = await _pridej(hass, "ustecky")
    r2 = await _pridej(hass, "vysocina")
    assert r1["title"] == "HZS Ústecký kraj – celý kraj"
    assert r2["title"] == "HZS Vysočina – celý kraj"
    assert _stav(hass, r1["result"].entry_id, "dnes").state == "4"
    assert _stav(hass, r2["result"].entry_id, "dnes").state == "13"
    zdroje = {hass.states.get(e).attributes["source"] for e in hass.states.async_entity_ids("geo_location")}
    assert zdroje == {DOMAIN}
    # 5 z Ústeckého (i ten ze 14. 10. 23:07 je mladší než 24 h) + 13 z Vysočiny
    assert len(hass.states.async_entity_ids("geo_location")) == 5 + 13
