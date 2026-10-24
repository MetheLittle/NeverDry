"""Select platform for the NeverDry integration.

One entity per config entry, on the hub device: the **operating mode** of the
Environment - active, away or stopped (``docs/design/scheduler.md``, section
9.4).

An entity rather than a field of the options form on purpose. Saving that form
reloads the entry, and a reload cuts short a run in progress (GH #282): a mode
switched to *away* on the morning of a trip must not interrupt the watering it
is meant to leave running. An entity can also be driven by an automation, which
is how a mode switched twice a year is most likely to be switched.
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .environment import OperatingMode

_LOGGER = logging.getLogger(__name__)


def mode_key(entry_id: str) -> str:
    """Where the declared mode waits in ``hass.data`` for a controller to read it.

    The sensor platform builds the controller and this platform restores the
    mode; Home Assistant sets the two up concurrently, so whichever comes second
    has to find what the first one left.
    """
    return f"_operating_mode_{entry_id}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the operating-mode select from a config entry (UI)."""
    async_add_entities([OperatingModeSelect(hass, entry.entry_id)])


class OperatingModeSelect(SelectEntity, RestoreEntity):
    """The declared operating mode of one Environment, restored across restarts."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:power-sleep"
    _attr_translation_key = "operating_mode"

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_operating_mode"
        self._attr_options = [mode.value for mode in OperatingMode]
        self._attr_current_option = OperatingMode.ACTIVE.value
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)})

    async def async_added_to_hass(self) -> None:
        """Restore the last declared mode, so a restart in winter stays stopped."""
        last = await self.async_get_last_state()
        if last is not None and last.state in self._attr_options:
            self._attr_current_option = last.state
        await self._apply(OperatingMode(self._attr_current_option))

    async def async_select_option(self, option: str) -> None:
        """Declare a new mode. Takes effect at once, without reloading the entry."""
        mode = OperatingMode(option)
        self._attr_current_option = mode.value
        await self._apply(mode)
        self.async_write_ha_state()

    async def _apply(self, mode: OperatingMode) -> None:
        """Hand the mode to the controller, or leave it for the controller to find."""
        domain_data = self._hass.data.setdefault(DOMAIN, {})
        domain_data[mode_key(self._entry_id)] = mode
        controller = domain_data.get(f"_controller_{self._entry_id}")
        if controller is not None:
            await controller.async_set_operating_mode(mode)
