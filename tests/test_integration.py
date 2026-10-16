"""Testy integrace v Home Assistantu (pytest-homeassistant-custom-component)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import re

import aiohttp
import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.hzs_zasahy.const import (
    DOMAIN,
    KRAJE,
    EVENT_NOVY_ZASAH,
    EVENT_ZMENA_STAVU,
)

FIX = Path(__file__).parent / "fixtures" / "vysocina"
CIS = json.loads((FIX / "ciselniky.json").read_text(encoding="utf-8"))
UDALOSTI = json.loads((FIX / "udalosti.json").read_text(encoding="utf-8"))
TECHNIKA = json.loads((FIX / "technika.json").read_text(encoding="utf-8"))
URL_VYS = KRAJE["vysocina"][1]
API = URL_VYS + "api/"

NOVY_POZAR = {
    "id": 421099061,
    "casOhlaseni": "2026-10-15T17:50:00.000Z",
    "casVzniku": None,
    "stavId": 420,
    "typId": 3100,
    "podtypId": 3110,
    "poznamkaProMedia": None,
    "kraj": {"id": 108, "nazev": "Vysočina"},
    "okres": {"id": 3707, "nazev": "Jihlava"},
    "obec": "Kněžice",
    "castObce": "Kněžice",
    "ORP": "Třešť",
    "ulice": None,
    "gis1": "680000",
    "gis2": "1140000",
    "zoc": False,
    "silnice": None,
}


def _registruj(mock, udalosti: list[dict]) -> None:
    mock.clear_requests()
    for k in ("typy", "podtypy", "stavy", "kraje", "okresy", "konfigurace"):
        mock.get(API + k, json=CIS[k])
    mock.get(re.compile(r"/udalosti/api/udalosti/\d+/technika"), json=TECHNIKA)
    mock.get(API, json=udalosti)


async def _obnov(hass: HomeAssistant) -> None:
    await hass.config_entries.async_entries(DOMAIN)[0].runtime_data.async_refresh()
    await hass.async_block_till_done()


@pytest.fixture
async def prostredi(hass: HomeAssistant, freezer):
    hass.config.language = "cs"
    await hass.config.async_set_time_zone("Europe/Prague")
    hass.config.latitude = 49.3961  # Jihlava
    hass.config.longitude = 15.5912
    freezer.move_to("2026-10-15 20:00:00+02:00")
    return hass


async def _nastav(hass: HomeAssistant, okres: str = "0", typy: list[str] | None = None):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"kraj": "vysocina"}
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "filtr"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"okres_id": okres, "typy": typy or []}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    return result


def _stav(hass: HomeAssistant, klic: str):
    from homeassistant.helpers import entity_registry as er

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    ent_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{klic}")
    return hass.states.get(ent_id)


async def test_cely_kraj_senzory_mapa_eventy(prostredi, aioclient_mock):
    hass = prostredi
    _registruj(aioclient_mock, UDALOSTI)
    result = await _nastav(hass)
    assert result["title"] == "HZS Vysočina – celý kraj"

    assert _stav(hass, "dnes").state == "13"
    assert _stav(hass, "aktivni").state == "0"
    assert _stav(hass, "tyden").state == "13"
    posledni = _stav(hass, "posledni")
    assert posledni.state == "Technická pomoc – otevření uzavřených prostor – Trnava"
    assert posledni.attributes["okres"] == "Třebíč"
    assert posledni.attributes["jednotky"] == ["CHS Jihlava"]
    assert _stav(hass, "posledni_cas").state == "2026-10-15T16:41:43+00:00"

    nejblizsi = _stav(hass, "nejblizsi")
    assert float(nejblizsi.state) < 3
    assert nejblizsi.attributes["obec"] == "Jihlava"

    geo = hass.states.async_entity_ids("geo_location")
    assert len(geo) == 13
    g = hass.states.get(geo[0])
    assert g.attributes["source"] == DOMAIN
    assert g.attributes["latitude"] > 48.9

    # dotaz bez okresu, ale se všemi stavy
    url = next(c[1] for c in aioclient_mock.mock_calls if c[1].path == "/udalosti/api/")
    assert "okresId" not in url.query
    assert "510" in url.query.getall("stavIds")
    assert url.query["krajId"] == "108"

    nove = async_capture_events(hass, EVENT_NOVY_ZASAH)
    zmeny = async_capture_events(hass, EVENT_ZMENA_STAVU)

    # Nový probíhající požár
    _registruj(aioclient_mock, [NOVY_POZAR, *UDALOSTI])
    await _obnov(hass)
    assert len(nove) == 1 and not zmeny
    d = nove[0].data
    assert d["nazev"] == "Požár – lesní porost – Kněžice"
    assert d["aktivni"] is True
    assert d["stav"] == "Otevřená, SaP na místě"
    assert d["jednotky"] == ["CHS Jihlava"]
    assert 5 < d["vzdalenost_km"] < 30
    assert _stav(hass, "aktivni").state == "1"
    assert _stav(hass, "dnes").state == "14"
    assert len(hass.states.async_entity_ids("geo_location")) == 14

    # Požár uzavřen -> změna stavu, žádný nový zásah
    uzavreny = dict(NOVY_POZAR, stavId=510)
    _registruj(aioclient_mock, [uzavreny, *UDALOSTI])
    await _obnov(hass)
    assert len(nove) == 1
    assert len(zmeny) == 1
    assert zmeny[0].data["predchozi_stav"] == "Otevřená, SaP na místě"
    assert zmeny[0].data["stav"] == "Uzavřená OS"
    assert zmeny[0].data["aktivni"] is False
    assert _stav(hass, "aktivni").state == "0"

    # Bez další změny nic nového
    await _obnov(hass)
    assert len(nove) == 1 and len(zmeny) == 1

    # Zásahy, které z API zmizí, zmizí i z mapy
    _registruj(aioclient_mock, [uzavreny])
    await _obnov(hass)
    assert hass.states.async_entity_ids("geo_location") == ["geo_location.pozar_lesni_porost_knezice"]


async def test_restart_neposila_stare_eventy(prostredi, aioclient_mock):
    hass = prostredi
    _registruj(aioclient_mock, UDALOSTI)
    nove = async_capture_events(hass, EVENT_NOVY_ZASAH)
    await _nastav(hass)
    await _obnov(hass)
    assert nove == []


async def test_okres_a_typy(prostredi, aioclient_mock):
    hass = prostredi
    _registruj(aioclient_mock, UDALOSTI)
    result = await _nastav(hass, okres="3707", typy=["3100"])
    assert result["title"] == "HZS Vysočina – okres Jihlava"
    url = next(c[1] for c in aioclient_mock.mock_calls if c[1].path == "/udalosti/api/")
    assert url.query["okresId"] == "3707"
    # fixture obsahuje 2 požáry (filtr okresu dělá server, mock vrací vše)
    assert _stav(hass, "dnes").state == "2"


async def test_options(prostredi, aioclient_mock):
    hass = prostredi
    _registruj(aioclient_mock, UDALOSTI)
    await _nastav(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"typy": ["3800"], "scan_interval": 3, "nacitat_techniku": False, "mapa": False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    c = entry.runtime_data
    assert c.update_interval.total_seconds() == 180
    assert _stav(hass, "dnes").state == "3"  # 3 plané poplachy
    assert _stav(hass, "posledni").attributes["jednotky"] == []
    assert hass.states.async_entity_ids("geo_location") == []


async def test_duplicitni_okres(prostredi, aioclient_mock):
    hass = prostredi
    _registruj(aioclient_mock, UDALOSTI)
    await _nastav(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"kraj": "vysocina"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"okres_id": "0", "typy": []}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_flow_nedostupny_server(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.get(re.compile(r"/udalosti/api/"), exc=aiohttp.ClientError())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"kraj": "vysocina"}
    )
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_flow_spatne_api(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.get(re.compile(r"/api/"), json={"neco": "jineho"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"kraj": "vlastni"}
    )
    assert result["step_id"] == "url"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"base_url": "http://example.com/udalosti"}
    )
    assert result["step_id"] == "url"
    assert result["errors"] == {"base": "invalid_api"}


async def test_vypadek_serveru(prostredi, aioclient_mock):
    hass = prostredi
    _registruj(aioclient_mock, UDALOSTI)
    await _nastav(hass)
    aioclient_mock.clear_requests()
    aioclient_mock.get(API, status=500, text='{"type":0}')
    await _obnov(hass)
    assert _stav(hass, "dnes").state == "unavailable"
    _registruj(aioclient_mock, copy.deepcopy(UDALOSTI))
    await _obnov(hass)
    assert _stav(hass, "dnes").state == "13"
