"""Všechny předvolené kraje + zvláštnosti jednotlivých webů (na vzorcích ostrých dat)."""

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

from custom_components.hzs_zasahy.api import Ciselniky, kandidati_api, parse_udalosti
from custom_components.hzs_zasahy.const import DOMAIN, KRAJE

FIX = Path(__file__).parent / "fixtures"


def _fix(kraj: str, soubor: str):
    return json.loads((FIX / kraj / f"{soubor}.json").read_text(encoding="utf-8"))


def _registruj(mock, api: str, kraj: str, *, konfigurace: bool = True) -> None:
    cis = _fix(kraj, "ciselniky")
    for k in ("typy", "podtypy", "stavy", "kraje", "okresy"):
        mock.get(api + k, json=cis[k])
    if konfigurace and "konfigurace" in cis:
        mock.get(api + "konfigurace", json=cis["konfigurace"])
    else:
        mock.get(api + "konfigurace", status=404, text="Cannot GET /konfigurace")
    mock.get(re.compile(re.escape(api) + r"udalosti/\d+/technika"), json=_fix(kraj, "technika"))
    mock.get(api, json=_fix(kraj, "udalosti"))


@pytest.fixture
async def prostredi(hass: HomeAssistant, freezer):
    hass.config.language = "cs"
    await hass.config.async_set_time_zone("Europe/Prague")
    hass.config.latitude = 49.1951  # Brno
    hass.config.longitude = 16.6068
    freezer.move_to("2026-10-16 23:00:00+02:00")
    return hass


async def _pridej(hass, volba: str, okres: str = "0", url: str | None = None, kraj_id: str | None = None):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"kraj": volba})
    if url is not None:
        assert result["step_id"] == "url"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"base_url": url})
    if kraj_id is not None:
        assert result["step_id"] == "vyber_kraje", result
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"kraj_id": kraj_id})
    assert result.get("step_id") == "filtr", result
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"okres_id": okres, "typy": []}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    return result


def _stav(hass, entry_id: str, klic: str):
    ent = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry_id}_{klic}")
    return hass.states.get(ent)


# volba, titulek, počet událostí ve vzorku, čas posledního zásahu (UTC), počet okresů
PREDVOLBY = [
    ("jihomoravsky", "HZS Jihomoravský kraj – celý kraj", 3, "2026-10-16T16:30:43+00:00", 7),
    # Královéhradecký: ověřena jen /api/konfigurace (krajId 86), vzorek událostí zatím nemáme
    ("kralovehradecky", "HZS Královéhradecký kraj – celý kraj", 0, None, 5),
    ("moravskoslezsky", "HZS Moravskoslezský kraj – celý kraj", 3, "2026-10-16T17:23:51+00:00", 6),
    ("ustecky", "HZS Ústecký kraj – celý kraj", 5, "2026-10-15T20:05:33+00:00", 7),
    ("vysocina", "HZS Vysočina – celý kraj", 13, "2026-10-15T16:41:43+00:00", 5),
    ("zlinsky", "HZS Zlínský kraj – celý kraj", 3, "2026-10-16T18:51:26+00:00", 4),
]


def test_predvolby_kompletni():
    webohled = [k for k, v in KRAJE.items() if v.zdroj == "webohled"]
    assert sorted(webohled) == sorted(p[0] for p in PREDVOLBY)


@pytest.mark.parametrize(("volba", "titulek", "pocet", "posledni", "okresu"), PREDVOLBY)
async def test_predvolba(prostredi, aioclient_mock, volba, titulek, pocet, posledni, okresu):
    hass = prostredi
    kraj = KRAJE[volba]
    # Moravskoslezský web /konfigurace nemá – musí stačit číslo kraje z předvolby
    _registruj(aioclient_mock, kraj.api, volba, konfigurace=volba != "moravskoslezsky")
    result = await _pridej(hass, volba)
    assert result["title"] == titulek
    assert result["data"]["api_url"] == kraj.api
    assert result["data"]["kraj_id"] == kraj.kraj_id
    eid = result["result"].entry_id

    dotaz = next(c[1] for c in aioclient_mock.mock_calls if str(c[1]).startswith(kraj.api + "?"))
    assert dotaz.query["krajId"] == str(kraj.kraj_id)

    assert _stav(hass, eid, "tyden").state == str(pocet)
    if posledni is None:
        assert _stav(hass, eid, "posledni_cas").state == "unknown"
    else:
        assert _stav(hass, eid, "posledni_cas").state == posledni
        # Technika se po startu načítá jen k dnešním a probíhajícím zásahům
        jednotky = _stav(hass, eid, "posledni").attributes["jednotky"]
        assert bool(jednotky) == posledni.startswith("2026-10-16")
    assert len(entry_cis(hass, eid).okresy) == okresu


def entry_cis(hass, entry_id) -> Ciselniky:
    return hass.config_entries.async_get_entry(entry_id).runtime_data.ciselniky


async def test_zlinsky_probihajici_v_mistnim_case(prostredi, aioclient_mock):
    hass = prostredi
    _registruj(aioclient_mock, KRAJE["zlinsky"].api, "zlinsky")
    result = await _pridej(hass, "zlinsky", okres="3705")
    eid = result["result"].entry_id
    assert result["title"] == "HZS Zlínský kraj – okres Zlín"
    aktivni = _stav(hass, eid, "aktivni")
    assert aktivni.state == "1"
    z = aktivni.attributes["zasahy"][0]
    # API: "2026-10-16T20:51:26" bez zóny = 20:51 místního času (tak to ukazuje i web)
    assert z["cas"] == "2026-10-16T20:51:26+02:00"
    assert z["nazev"] == "Dopravní nehoda – úklid vozovky – Otrokovice"
    assert z["jednotky"] == ["HS Otrokovice"]


def test_kandidati_api():
    assert kandidati_api("https://webohledzlk.hzscr.cz/udalosti/") == [
        "https://webohledzlk.hzscr.cz/udalosti/api/",
        "https://webohledzlk.hzscr.cz/api/",
    ]
    assert kandidati_api("udalosti.firebrno.cz") == ["https://udalosti.firebrno.cz/api/"]
    assert kandidati_api("http://webohled.hzsmsk.cz/api/") == ["http://webohled.hzsmsk.cz/api/"]


async def test_vlastni_adresa_najde_api_na_koreni(prostredi, aioclient_mock):
    """Zlínský: web na /udalosti/, API na /api/ – integrace to dohledá sama."""
    hass = prostredi
    aioclient_mock.get("https://webohledzlk.hzscr.cz/udalosti/api/typy", status=404, text="<html>404</html>")
    _registruj(aioclient_mock, "https://webohledzlk.hzscr.cz/api/", "zlinsky")
    result = await _pridej(hass, "vlastni", url="webohledzlk.hzscr.cz/udalosti")
    assert result["title"] == "HZS Zlínský kraj – celý kraj"
    assert result["data"]["api_url"] == "https://webohledzlk.hzscr.cz/api/"
    assert result["data"]["base_url"] == "https://webohledzlk.hzscr.cz/udalosti/"


async def test_vlastni_adresa_bez_konfigurace_se_zepta_na_kraj(prostredi, aioclient_mock):
    """Moravskoslezský přes vlastní adresu: kraj neřekne, tak se zeptáme."""
    hass = prostredi
    _registruj(aioclient_mock, "http://webohled.hzsmsk.cz/api/", "moravskoslezsky", konfigurace=False)
    result = await _pridej(hass, "vlastni", url="http://webohled.hzsmsk.cz/", kraj_id="132")
    assert result["title"] == "HZS Moravskoslezský kraj – celý kraj"
    assert result["data"]["kraj_id"] == 132


def test_msk_bez_pole_silnice():
    cis = Ciselniky.z_api(_fix("moravskoslezsky", "ciselniky"), kraj_id=132)
    u = parse_udalosti(_fix("moravskoslezsky", "udalosti"), cis)
    assert u[0].podtyp == "neakutní otevření uzavřených prostor"
    assert u[0].silnice is None
    assert u[0].cas == datetime(2026, 10, 16, 17, 23, 51, tzinfo=timezone.utc)
    # Ostrava, Hrabůvka
    assert 49.7 < u[0].latitude < 49.85 and 18.2 < u[0].longitude < 18.35


def test_okresy_jen_vlastniho_kraje():
    cis = Ciselniky.z_api(_fix("jihomoravsky", "ciselniky"))
    assert "Jihlava" not in cis.okresy.values()
    assert cis.kraj == "Jihomoravský kraj"


def test_bez_konfigurace_a_bez_kraje():
    cis = Ciselniky.z_api(_fix("moravskoslezsky", "ciselniky"))
    assert cis.kraj_id is None
    assert cis.okresy == {}
    assert cis.pro_kraj(132).kraj == "Moravskoslezský kraj"
