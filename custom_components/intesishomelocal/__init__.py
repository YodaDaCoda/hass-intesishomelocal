"""The IntesisHome integration."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

DOMAIN = "intesishomelocal"
PLATFORMS = ["climate"]

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up IntesisHome from a config entry."""
    _LOGGER.info(f"Setting up {DOMAIN} integration for entry: %s", entry.entry_id)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception as e:
        _LOGGER.error("Error setting up platforms for entry %s: %s", entry.entry_id, e)
        return False

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info(f"Unloading {DOMAIN} integration for entry: %s", entry.entry_id)
    try:
        unload_ok = all(
            await asyncio.gather(
                *[hass.config_entries.async_forward_entry_unload(entry, platform) for platform in PLATFORMS]
            )
        )
    except Exception as e:
        _LOGGER.error("Error unloading platforms for entry %s: %s", entry.entry_id, e)
        return False

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok