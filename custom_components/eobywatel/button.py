from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .sensor import EobywatelCoordinator


class MarkAllReadButton(CoordinatorEntity[EobywatelCoordinator], ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Oznacz wszystkie jako przeczytane"
    _attr_icon = "mdi:email-check-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EobywatelCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_mark_all_notifications_read"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.entry.entry_id)},
            "name": "e-Obywatel (KKOnline)",
            "manufacturer": "e-Obywatel / Portal Mieszkańca",
            "model": "KKOnline",
        }

    async def async_press(self) -> None:
        await self.coordinator.mark_all_notifications_read()


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MarkAllReadButton(coordinator)])
