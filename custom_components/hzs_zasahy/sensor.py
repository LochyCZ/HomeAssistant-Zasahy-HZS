"""Senzory zásahů HZS."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import Udalost
from .coordinator import HzsConfigEntry, HzsCoordinator
from .entity import device_info

# Víc položek do atributu nedáváme – frontend by tahal zbytečně velký stav
MAX_V_ATRIBUTU = 50


def _dnes(c: HzsCoordinator) -> list[Udalost]:
    today = dt_util.now().date()
    return [u for u in c.data.udalosti if dt_util.as_local(u.cas).date() == today]


def _aktivni(c: HzsCoordinator) -> list[Udalost]:
    return [u for u in c.data.udalosti if u.aktivni]


def _seznam(c: HzsCoordinator, udalosti: list[Udalost]) -> dict[str, Any]:
    return {"zasahy": [c.udalost_dict(u) for u in udalosti[:MAX_V_ATRIBUTU]]}


def _nejblizsi_dnes(c: HzsCoordinator) -> Udalost | None:
    s_vzdalenosti = [(c.vzdalenost(u), u) for u in _dnes(c)]
    s_vzdalenosti = [(d, u) for d, u in s_vzdalenosti if d is not None]
    return min(s_vzdalenosti, key=lambda x: x[0])[1] if s_vzdalenosti else None


def _posledni_attrs(c: HzsCoordinator) -> dict[str, Any]:
    if not c.data.udalosti:
        return {}
    data = c.udalost_dict(c.data.udalosti[0])
    data.pop("nazev")
    return data


def _nejblizsi_attrs(c: HzsCoordinator) -> dict[str, Any]:
    u = _nejblizsi_dnes(c)
    if u is None:
        return {}
    data = c.udalost_dict(u)
    data.pop("vzdalenost_km")
    return data


@dataclass(frozen=True, kw_only=True)
class HzsSensorDescription(SensorEntityDescription):
    value_fn: Callable[[HzsCoordinator], Any]
    attrs_fn: Callable[[HzsCoordinator], dict[str, Any]] | None = None


SENSORS: tuple[HzsSensorDescription, ...] = (
    HzsSensorDescription(
        key="dnes",
        translation_key="dnes",
        icon="mdi:fire-truck",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: len(_dnes(c)),
        attrs_fn=lambda c: _seznam(c, _dnes(c)),
    ),
    HzsSensorDescription(
        key="aktivni",
        translation_key="aktivni",
        icon="mdi:fire-alert",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: len(_aktivni(c)),
        attrs_fn=lambda c: _seznam(c, _aktivni(c)),
    ),
    HzsSensorDescription(
        key="tyden",
        translation_key="tyden",
        icon="mdi:format-list-bulleted",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: len(c.data.udalosti),
        attrs_fn=lambda c: _seznam(c, c.data.udalosti),
    ),
    HzsSensorDescription(
        key="posledni",
        translation_key="posledni",
        icon="mdi:fire",
        value_fn=lambda c: c.data.udalosti[0].nazev[:255] if c.data.udalosti else None,
        attrs_fn=_posledni_attrs,
    ),
    HzsSensorDescription(
        key="posledni_cas",
        translation_key="posledni_cas",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: c.data.udalosti[0].cas if c.data.udalosti else None,
    ),
    HzsSensorDescription(
        key="nejblizsi",
        translation_key="nejblizsi",
        icon="mdi:map-marker-distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=1,
        value_fn=lambda c: (c.vzdalenost(u) if (u := _nejblizsi_dnes(c)) else None),
        attrs_fn=_nejblizsi_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HzsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(HzsSensor(coordinator, desc) for desc in SENSORS)


class HzsSensor(CoordinatorEntity[HzsCoordinator], SensorEntity):
    _attr_has_entity_name = True
    # Seznamy a texty v atributech nechceme v databázi recorderu
    _unrecorded_attributes = frozenset(
        {"zasahy", "technika", "jednotky", "poznamka", "misto"}
    )
    entity_description: HzsSensorDescription

    def __init__(self, coordinator: HzsCoordinator, desc: HzsSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = desc
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{desc.key}"
        self._attr_device_info = device_info(coordinator)

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator) if fn else None
