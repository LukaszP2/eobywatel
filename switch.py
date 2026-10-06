from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .sensor import EobywatelCoordinator

ICONS = {
    "2": "mdi:credit-card-check-outline",
    "3": "mdi:gauge",
    "4": "mdi:calendar-clock",
    "6": "mdi:alert-outline",
}


class SubscriptionSwitch(CoordinatorEntity[EobywatelCoordinator], SwitchEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EobywatelCoordinator, category: dict) -> None:
        super().__init__(coordinator)
        self.category_id = str(category["id"])
        self._attr_unique_id = f"{DOMAIN}_subscription_{self.category_id}"
        self._attr_name = category["name"]
        self._attr_icon = ICONS.get(self.category_id, "mdi:bell-outline")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="e-Obywatel (KKOnline)",
            manufacturer="e-Obywatel / Portal Mieszkańca",
        )

    @property
    def is_on(self) -> bool:
        for category in (self.coordinator.data or {}).get("subscriptions", {}).get("categories", []):
            if str(category.get("id")) == self.category_id:
                return bool(category.get("enabled"))
        return False

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.set_subscription(self.category_id, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.set_subscription(self.category_id, False)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    categories = (coordinator.data or {}).get("subscriptions", {}).get("categories", [])
    async_add_entities([SubscriptionSwitch(coordinator, category) for category in categories])
