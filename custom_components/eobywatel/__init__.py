"""e-Obywatel integration for Home Assistant."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .sensor import EobywatelCoordinator

PLATFORMS = ["sensor", "switch", "button"]
SERVICE_SUBMIT_METER_READING = "submit_meter_reading"

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("meter"): cv.entity_id,
        vol.Required("reading"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})

    async def handle_submit_meter_reading(call):
        entity_id = str(call.data["meter"])
        reading = float(call.data["reading"])
        registry = er.async_get(hass)
        entity = registry.async_get(entity_id)

        if entity is None or entity.platform != DOMAIN:
            raise ServiceValidationError(
                f"Wybrany sensor {entity_id} nie należy do integracji e-Obywatel"
            )

        unique_id = entity.unique_id
        prefix = f"{DOMAIN}_meter_"
        suffix = "_reading"
        if not unique_id.startswith(prefix) or not unique_id.endswith(suffix):
            raise ServiceValidationError(
                f"Wybrany sensor {entity_id} nie jest sensorem odczytu wodomierza"
            )

        meter_id = unique_id[len(prefix) : -len(suffix)]

        for coordinator in hass.data[DOMAIN].values():
            meters = (coordinator.data or {}).get("meters", [])
            meter = next(
                (m for m in meters if str(m.get("meter_id")) == meter_id),
                None,
            )
            if meter is None:
                continue

            current = meter.get("reading_value")
            if current is not None and reading < float(current):
                raise ServiceValidationError(
                    f"Odczyt wodomierza {meter_id} nie może być mniejszy od obecnego "
                    f"stanu {current:g} m³"
                )

            await coordinator.submit_meter_reading(meter_id, reading)
            return

        raise ServiceValidationError(
            f"Wodomierz {meter_id} nie został znaleziony w aktywnej konfiguracji e-Obywatel"
        )

    hass.services.async_register(
        DOMAIN, SERVICE_SUBMIT_METER_READING, handle_submit_meter_reading, SERVICE_SCHEMA
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = EobywatelCoordinator(hass, entry)
    await coordinator.async_load_submission_state()
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(f"Nie można połączyć z e-Obywatelem: {err}") from err
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok
