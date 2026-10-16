"""Testy parseru a převodu souřadnic na vzorku skutečných dat."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from custom_components.hzs_zasahy.api import (
    Ciselniky,
    WebohledError,
    hezky,
    parse_techniku,
    parse_udalosti,
)
from custom_components.hzs_zasahy.krovak import krovak_to_wgs84, vzdalenost_km

FIX = Path(__file__).parent / "fixtures" / "vysocina"
CIS_RAW = json.loads((FIX / "ciselniky.json").read_text(encoding="utf-8"))
UDALOSTI_RAW = json.loads((FIX / "udalosti.json").read_text(encoding="utf-8"))
TECHNIKA_RAW = json.loads((FIX / "technika.json").read_text(encoding="utf-8"))


@pytest.fixture
def cis() -> Ciselniky:
    return Ciselniky.z_api(CIS_RAW)


def test_ciselniky(cis):
    assert cis.kraj_id == 108
    assert cis.kraj == "Vysočina"
    assert set(cis.okresy.values()) == {
        "Havlíčkův Brod", "Jihlava", "Pelhřimov", "Třebíč", "Žďár nad Sázavou"
    }
    assert cis.stavy[510] == "Uzavřená OS"


def test_parse_udalosti(cis):
    u = parse_udalosti(UDALOSTI_RAW, cis)
    assert len(u) == 13
    assert u == sorted(u, key=lambda x: x.cas, reverse=True)
    pozar = next(x for x in u if x.id == 421088061)
    assert pozar.typ == "Požár"
    assert pozar.podtyp == "polní porost, tráva"
    assert pozar.nazev == "Požár – polní porost, tráva – Střítež"
    assert pozar.stav == "Uzavřená OS"
    assert not pozar.aktivni
    assert pozar.okres == "Jihlava"
    assert pozar.cas == datetime(2026, 10, 15, 14, 58, 26, tzinfo=timezone.utc)
    assert pozar.as_dict()["cas"] == "2026-10-15T16:58:26+02:00"
    assert pozar.poznamka.startswith("Požár pařezu")


def test_nazev_bez_duplicity(cis):
    u = next(x for x in parse_udalosti(UDALOSTI_RAW, cis) if x.typ_id == 3800)
    assert u.nazev.startswith("Planý poplach – ")
    assert u.nazev.count("Planý poplach") == 1


def test_misto(cis):
    u = next(x for x in parse_udalosti(UDALOSTI_RAW, cis) if x.id == 421089061)
    assert u.misto == "Telč, Telč-Staré Město, Radkovská"


def test_souradnice(cis):
    u = next(x for x in parse_udalosti(UDALOSTI_RAW, cis) if x.id == 421080061)
    # Jihlava, Brněnská (ověřeno proti pyproj EPSG:5514 -> EPSG:4326)
    assert u.latitude == pytest.approx(49.39747, abs=1e-4)
    assert u.longitude == pytest.approx(15.60295, abs=1e-4)


@pytest.mark.parametrize(
    ("y", "x", "lat", "lon"),
    [
        (681827, 1153338, 49.17477, 15.45614),  # Telč
        (641628, 1113359, 49.57489, 15.94000),  # Žďár nad Sázavou
        (788000, 1018000, 50.25291, 13.74565),  # Rakovník
    ],
)
def test_krovak(y, x, lat, lon):
    a, b = krovak_to_wgs84(y, x)
    assert a == pytest.approx(lat, abs=2e-5)
    assert b == pytest.approx(lon, abs=2e-5)


def test_vzdalenost():
    # Jihlava – Třebíč vzdušnou čarou cca 29 km
    assert vzdalenost_km(49.3961, 15.5912, 49.2149, 15.8817) == pytest.approx(29, abs=1.5)


def test_chybejici_souradnice(cis):
    raw = dict(UDALOSTI_RAW[0], gis1=None, gis2="")
    u = parse_udalosti([raw], cis)[0]
    assert u.latitude is None and u.longitude is None


def test_neznamy_typ(cis):
    raw = dict(UDALOSTI_RAW[0], typId=9999, podtypId=None)
    u = parse_udalosti([raw], cis)[0]
    assert u.typ == "9999"
    assert u.podtyp == ""


def test_spatna_data(cis):
    with pytest.raises(WebohledError):
        parse_udalosti({"type": 0}, cis)
    with pytest.raises(WebohledError):
        parse_udalosti([{"id": 1}], cis)


def test_technika():
    t = parse_techniku(TECHNIKA_RAW)
    assert len(t) == 2
    assert t[0].jednotka == "CHS Jihlava"
    assert parse_techniku({"x": 1}) == ()


def test_hezky():
    assert hezky("POLNÍ POROST, TRÁVA") == "polní porost, tráva"
    assert hezky("SIGNALIZACE EPS") == "signalizace EPS"
    assert hezky("ZÁCHRANA OSOB A ZVÍŘAT", velke_prvni=True) == "Záchrana osob a zvířat"
    assert hezky("AED") == "AED"


@pytest.mark.parametrize(
    ("silnice", "ocekavano"),
    [("D1", "D1"), ("     3. třídy  34524", "3. třídy 34524"), ("...", None), (".", None), ("0", None), ("nezjištěno", None), (None, None)],
)
def test_silnice(cis, silnice, ocekavano):
    raw = dict(UDALOSTI_RAW[0], silnice=silnice)
    u = parse_udalosti([raw], cis)[0]
    assert u.silnice == ocekavano
    assert "silnice" not in u.misto


def test_probihajici_zasah_z_ostrych_dat(cis):
    # skutečný probíhající zásah zachycený z API (stav 420)
    raw = {"id": 421095061, "casOhlaseni": "2026-10-15T18:47:36.000Z", "casVzniku": None, "stavId": 420,
           "typId": 3200, "podtypId": 3211, "poznamkaProMedia": None, "kraj": {"id": 108, "nazev": "Vysočina"},
           "okres": {"id": 3601, "nazev": "Havlíčkův Brod"}, "obec": "Krucemburk", "castObce": "Staré Ransko",
           "ORP": "Chotěboř", "ulice": None, "gis1": "651186", "gis2": "1099731", "zoc": False, "silnice": None}
    cis_s_podtypem = Ciselniky.z_api({**CIS_RAW, "podtypy": [*CIS_RAW["podtypy"], {"id": 3211, "nazev": "VYPROŠTĚNÍ OSOB", "typId": 3200}]})
    u = parse_udalosti([raw], cis_s_podtypem)[0]
    assert u.aktivni
    assert u.stav == "Otevřená, SaP na místě"
    assert u.nazev == "Dopravní nehoda – vyproštění osob – Krucemburk"
    assert u.misto == "Krucemburk, Staré Ransko"
    assert u.poznamka is None
    assert 49.6 < u.latitude < 49.75 and 15.75 < u.longitude < 15.95
