"""Výběr zdroje dat podle záznamu integrace."""

from __future__ import annotations

from typing import Any

import aiohttp

from .api import WebohledApi
from .const import CONF_API_URL, CONF_BASE_URL, CONF_ZDROJ, ZDROJ_HZSPA, ZDROJ_LBC, ZDROJ_PKR
from .hzspa import HzspaApi
from .lbc import LbcApi
from .pkr import PkrApi

type Zdroj = WebohledApi | PkrApi | HzspaApi | LbcApi


def vytvor_api(session: aiohttp.ClientSession, data: dict[str, Any], user_agent: str) -> Zdroj:
    """WebOhled (většina krajů), Portál krizového řízení (Středočeský, Liberecký) nebo hzspa.cz."""
    zdroj = data.get(CONF_ZDROJ)
    if zdroj == ZDROJ_PKR:
        return PkrApi(session, user_agent)
    if zdroj == ZDROJ_HZSPA:
        return HzspaApi(session, user_agent)
    if zdroj == ZDROJ_LBC:
        return LbcApi(session, user_agent)
    url = data.get(CONF_API_URL) or data[CONF_BASE_URL].rstrip("/") + "/api/"
    return WebohledApi(session, url, user_agent)
