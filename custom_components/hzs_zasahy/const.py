"""Konstanty integrace Zásahy HZS."""

from typing import NamedTuple

DOMAIN = "hzs_zasahy"


ZDROJ_WEBOHLED = "webohled"
ZDROJ_PKR = "pkr"
ZDROJ_HZSPA = "hzspa"


class Kraj(NamedTuple):
    popis: str
    web: str
    api: str
    kraj_id: int
    zdroj: str = ZDROJ_WEBOHLED


# Známé zdroje, ověřené na živých datech. Většina krajů má Přehled událostí HZS
# (systém WebOhled); Středočeský jen Portál krizového řízení (ZDROJ_PKR, viz pkr.py)
# a Pardubický vlastní web hzspa.cz (ZDROJ_HZSPA, viz hzspa.py).
# Adresu API a číslo kraje držíme zvlášť: Zlínský kraj má API jinde než web
# a Moravskoslezský nemá /api/konfigurace, ze které by šlo číslo kraje zjistit.
KRAJE: dict[str, Kraj] = {
    "jihomoravsky": Kraj(
        "Jihomoravský kraj", "https://udalosti.firebrno.cz/", "https://udalosti.firebrno.cz/api/", 116
    ),
    "kralovehradecky": Kraj(
        "Královéhradecký kraj",
        "https://udalostikhk.hzscr.cz/",
        "https://udalostikhk.hzscr.cz/api/",
        86,
    ),
    "moravskoslezsky": Kraj(
        "Moravskoslezský kraj", "http://webohled.hzsmsk.cz/", "http://webohled.hzsmsk.cz/api/", 132
    ),
    "pardubicky": Kraj(
        "Pardubický kraj",
        "https://www.hzspa.cz/vyjezdy/aktualni-vyjezdy.php",
        "https://www.hzspa.cz/vyjezdy/",
        94,
        ZDROJ_HZSPA,
    ),
    "stredocesky": Kraj(
        "Středočeský kraj",
        "https://pkr.kr-stredocesky.cz/pkr/zasahy-jpo/",
        "https://pkr.kr-stredocesky.cz/pkr/zasahy-jpo/",
        27,
        ZDROJ_PKR,
    ),
    "ustecky": Kraj(
        "Ústecký kraj", "https://udalosti.hzsulk.cz/", "https://udalosti.hzsulk.cz/api/", 60
    ),
    "vysocina": Kraj(
        "Kraj Vysočina",
        "http://webohled.hasici-vysocina.cz/udalosti/",
        "http://webohled.hasici-vysocina.cz/udalosti/api/",
        108,
    ),
    "zlinsky": Kraj(
        "Zlínský kraj",
        "https://webohledzlk.hzscr.cz/udalosti/",
        "https://webohledzlk.hzscr.cz/api/",
        141,
    ),
}
VLASTNI = "vlastni"

CONF_KRAJ = "kraj"
CONF_BASE_URL = "base_url"
CONF_API_URL = "api_url"
CONF_KRAJ_ID = "kraj_id"
CONF_ZDROJ = "zdroj"
CONF_OKRES = "okres_id"
CONF_TYPY = "typy"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_TECHNIKA = "nacitat_techniku"
CONF_MAPA = "mapa"

CELY_KRAJ = "0"

DEFAULT_SCAN_INTERVAL = 5  # minut
DEFAULT_SCAN_INTERVAL_PKR = 10  # Portál krizového řízení: data jsou zpožděná a web nevítá roboty
MIN_SCAN_INTERVAL = 2
MAX_SCAN_INTERVAL = 60

# Jak daleko do minulosti stahujeme události
OKNO_DNI = 7
# Kolik hodin drží zásah entitu na mapě
MAPA_HODIN = 24

EVENT_NOVY_ZASAH = f"{DOMAIN}_novy_zasah"
EVENT_ZMENA_STAVU = f"{DOMAIN}_zmena_stavu"

USER_AGENT = "HomeAssistant-hzs_zasahy/1.0 (osobni pouziti)"
