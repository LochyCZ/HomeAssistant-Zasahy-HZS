"""Config flow pro Zásahy HZS."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    Ciselniky,
    WebohledConnectionError,
    WebohledError,
    hezky,
    najdi_api,
)
from .const import (
    CELY_KRAJ,
    CONF_API_URL,
    CONF_BASE_URL,
    CONF_KRAJ,
    CONF_KRAJ_ID,
    CONF_MAPA,
    CONF_OKRES,
    CONF_SCAN_INTERVAL,
    CONF_TECHNIKA,
    CONF_TYPY,
    CONF_ZDROJ,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_PKR,
    DOMAIN,
    KRAJE,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    USER_AGENT,
    VLASTNI,
    ZDROJ_PKR,
    ZDROJ_WEBOHLED,
)
from .zdroje import Zdroj, vytvor_api


def _normalizuj_url(url: str) -> str:
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    if not url.endswith("/"):
        url += "/"
    return url


def _typy_selector(cis: Ciselniky) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(value=str(i), label=hezky(n, velke_prvni=True))
                for i, n in sorted(cis.typy.items(), key=lambda x: x[0])
            ],
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
        )
    )


def _chyba(err: WebohledError) -> str:
    return "cannot_connect" if isinstance(err, WebohledConnectionError) else "invalid_api"


class HzsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._kraj: str = next(iter(KRAJE))
        self._web: str = ""
        self._api: str = ""
        self._cis: Ciselniky | None = None
        self._zdroj: str = ZDROJ_WEBOHLED

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._kraj = user_input[CONF_KRAJ]
            if self._kraj == VLASTNI:
                return await self.async_step_url()
            kraj = KRAJE[self._kraj]
            self._web, self._api, self._zdroj = kraj.web, kraj.api, kraj.zdroj
            api: Zdroj = vytvor_api(
                async_get_clientsession(self.hass),
                {CONF_ZDROJ: kraj.zdroj, CONF_API_URL: kraj.api, CONF_BASE_URL: kraj.web},
                USER_AGENT,
            )
            try:
                self._cis = await api.ciselniky(kraj.kraj_id)
            except WebohledError as err:
                errors["base"] = _chyba(err)
            else:
                return await self.async_step_filtr()

        moznosti = [selector.SelectOptionDict(value=k, label=v.popis) for k, v in KRAJE.items()]
        moznosti.append(selector.SelectOptionDict(value=VLASTNI, label="Jiná adresa…"))
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_KRAJ, default=self._kraj): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=moznosti, mode=selector.SelectSelectorMode.LIST
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_url(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Vlastní adresa – API dohledáme a kraj zjistíme z /konfigurace."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._web = _normalizuj_url(user_input[CONF_BASE_URL])
            self._zdroj = ZDROJ_WEBOHLED
            session = async_get_clientsession(self.hass)
            try:
                api = await najdi_api(session, self._web, USER_AGENT)
                self._api = api.api_url
                self._cis = await api.ciselniky()
            except WebohledError as err:
                errors["base"] = _chyba(err)
            else:
                if self._cis.kraj_id is None:
                    return await self.async_step_vyber_kraje()
                return await self.async_step_filtr()

        return self.async_show_form(
            step_id="url",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BASE_URL, default=self._web or "https://"
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_vyber_kraje(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Starší weby neřeknou, který kraj je jejich – zeptáme se."""
        cis = self._cis
        assert cis is not None
        if user_input is not None:
            self._cis = cis.pro_kraj(int(user_input[CONF_KRAJ_ID]))
            return await self.async_step_filtr()

        return self.async_show_form(
            step_id="vyber_kraje",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_KRAJ_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=str(i), label=n)
                                for i, n in sorted(cis.kraje.items(), key=lambda x: x[1])
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_filtr(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        cis = self._cis
        assert cis is not None and cis.kraj_id is not None

        if user_input is not None:
            okres = str(user_input[CONF_OKRES])
            await self.async_set_unique_id(f"{self._api}|{cis.kraj_id}|{okres}")
            self._abort_if_unique_id_configured()
            nazev_okresu = (
                "celý kraj" if okres == CELY_KRAJ else f"okres {cis.okresy[int(okres)]}"
            )
            return self.async_create_entry(
                title=f"HZS {cis.kraj} – {nazev_okresu}",
                data={
                    CONF_BASE_URL: self._web,
                    CONF_API_URL: self._api,
                    CONF_KRAJ_ID: cis.kraj_id,
                    CONF_OKRES: okres,
                    CONF_ZDROJ: self._zdroj,
                },
                options={
                    CONF_TYPY: user_input.get(CONF_TYPY, []),
                    CONF_SCAN_INTERVAL: (
                        DEFAULT_SCAN_INTERVAL_PKR
                        if self._zdroj == ZDROJ_PKR
                        else DEFAULT_SCAN_INTERVAL
                    ),
                    CONF_TECHNIKA: True,
                    CONF_MAPA: True,
                },
            )

        okresy = [selector.SelectOptionDict(value=CELY_KRAJ, label="Celý kraj")] + [
            selector.SelectOptionDict(value=str(i), label=n)
            for i, n in sorted(cis.okresy.items(), key=lambda x: x[1])
        ]
        return self.async_show_form(
            step_id="filtr",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_OKRES, default=CELY_KRAJ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=okresy, mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    ),
                    vol.Optional(CONF_TYPY, default=[]): _typy_selector(cis),
                }
            ),
            description_placeholders={"kraj": cis.kraj},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> HzsOptionsFlow:
        return HzsOptionsFlow()


class HzsOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        opts = self.config_entry.options
        cis: Ciselniky = self.config_entry.runtime_data.ciselniky
        schema: dict = {
            vol.Optional(CONF_TYPY, default=opts.get(CONF_TYPY, [])): _typy_selector(cis),
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL,
                    max=MAX_SCAN_INTERVAL,
                    step=1,
                    unit_of_measurement="min",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
        # Středočeský a Pardubický web nemají techniku ze stejného API ani souřadnice
        if self.config_entry.data.get(CONF_ZDROJ, ZDROJ_WEBOHLED) == ZDROJ_WEBOHLED:
            schema[vol.Required(CONF_TECHNIKA, default=opts.get(CONF_TECHNIKA, True))] = bool
            schema[vol.Required(CONF_MAPA, default=opts.get(CONF_MAPA, True))] = bool
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
