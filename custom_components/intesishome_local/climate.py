"""Support for IntesisHome and airconwithme Smart AC Controllers."""

from __future__ import annotations

import logging
from typing import NamedTuple

from pyintesishome import (
    IHAuthenticationError,
    IHConnectionError,
    IntesisBase,
    IntesisHomeLocal,
)

from homeassistant import config_entries, core
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
    SWING_OFF,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    UnitOfTemperature,
)
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.entity import DeviceInfo


from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

MAP_IH_TO_HVAC_MODE = {
    "auto": HVACMode.HEAT_COOL,
    "cool": HVACMode.COOL,
    "dry": HVACMode.DRY,
    "fan": HVACMode.FAN_ONLY,
    "heat": HVACMode.HEAT,
    "off": HVACMode.OFF,
}
MAP_HVAC_MODE_TO_IH = {v: k for k, v in MAP_IH_TO_HVAC_MODE.items()}

MAP_IH_TO_PRESET_MODE = {
    "eco": PRESET_ECO,
    "comfort": PRESET_COMFORT,
    "powerful": PRESET_BOOST,
}
MAP_PRESET_MODE_TO_IH = {v: k for k, v in MAP_IH_TO_PRESET_MODE.items()}

MAP_SWING_TO_IH = {
    SWING_OFF: "auto/stop",
    "Position 1": "manual1",
    "Position 2": "manual2",
    "Position 3": "manual3",
    "Position 4": "manual4",
    "Position 5": "manual5",
    "Position 6": "manual6",
    "Position 7": "manual7",
    "Position 8": "manual8",
    "Position 9": "manual9",
    "Swing": "swing",
    "Swirl": "swirl",
    "wide": "wide",
}
MAP_IH_TO_SWING = {v: k for k, v in MAP_SWING_TO_IH.items()}

MAP_HORIZONTAL_SWING_TO_IH = {
    SWING_OFF: "auto/stop",
    "Position 1": "manual1",
    "Position 2": "manual2",
    "Position 3": "manual3",
    "Position 4": "manual4",
    "Position 5": "manual5",
    "Position 6": "manual6",
    "Position 7": "manual7",
    "Position 8": "manual8",
    "Position 9": "manual9",
    "Swing": "swing",
    "Swirl": "swirl",
    "Wide": "wide",
}
MAP_IH_TO_HORIZONTAL_SWING = {v: k for k, v in MAP_HORIZONTAL_SWING_TO_IH.items()}

MAP_STATE_ICONS = {
    HVACMode.COOL: "mdi:snowflake",
    HVACMode.DRY: "mdi:water-off",
    HVACMode.FAN_ONLY: "mdi:fan",
    HVACMode.HEAT: "mdi:white-balance-sunny",
    HVACMode.HEAT_COOL: "mdi:cached",
}

MAX_RETRIES = 10
MAX_WAIT_TIME = 300


async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    """Create climate entities from config flow."""

    if (
        "controller" in hass.data[DOMAIN]
        and config_entry.unique_id in hass.data[DOMAIN]["controller"]
    ):
        controller = hass.data[DOMAIN]["controller"].pop(config_entry.unique_id)
        ih_devices = controller.get_devices()
        if ih_devices:
            async_add_entities(
                [
                    IntesisAC(ih_device_id, device, controller)
                    for ih_device_id, device in ih_devices.items()
                ],
                update_before_add=False,
            )
        return True

    controller = IntesisHomeLocal(
        config_entry.data.get(CONF_HOST),
        config_entry.data.get(CONF_USERNAME),
        config_entry.data.get(CONF_PASSWORD),
        loop=hass.loop,
        websession=async_get_clientsession(hass),
    )

    try:
        await controller.poll_status()
    except IHAuthenticationError:
        _LOGGER.error("Invalid username or password")
        await controller.stop()
        return False
    except IHConnectionError as ex:
        _LOGGER.error(f"Error connecting to the {DOMAIN} API")
        raise PlatformNotReady from ex

    if ih_devices := controller.get_devices():
        async_add_entities(
            [
                IntesisAC(ih_device_id, device, controller)
                for ih_device_id, device in ih_devices.items()
            ],
            update_before_add=False,
        )
        return True

    _LOGGER.error(
        f"Error getting device list from {DOMAIN} API: %s",
        controller.error_message,
    )
    await controller.stop()
    return False


class IntesisAC(ClimateEntity):
    """Represents an Intesishome air conditioning device."""

    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, ih_device_id, ih_device, controller) -> None:
        """Initialize the thermostat."""
        self._controller: IntesisBase = controller
        self._device_id: str = ih_device_id
        self._ih_device: dict[str, dict[str, object]] = ih_device
        self._device_name: str = ih_device.get("name")
        self._device_type: str = controller.device_type
        self._connected: bool = False
        self._setpoint_step: float = 1.0
        self._current_temp: float = None
        self._max_temp: float = None
        self._attr_hvac_modes = []
        self._min_temp: int = None
        self._target_temp: float = None
        self._outdoor_temp: float = None
        self._hvac_mode: HVACMode = None
        self._preset: str = None
        self._preset_list: list[str] = [PRESET_ECO, PRESET_COMFORT, PRESET_BOOST]
        self._run_hours: int = None
        self._rssi = None
        self._swing_list: list[str] = []
        self._swing_horizontal_list: list[str] = []
        self._vvane: str = None
        self._hvane: str = None
        self._power: bool = False
        self._fan_speed = None
        self._attr_supported_features = 0
        self._power_consumption_heat = None
        self._power_consumption_cool = None

        # On / off support
        self._attr_supported_features |= ClimateEntityFeature.TURN_ON
        self._attr_supported_features |= ClimateEntityFeature.TURN_OFF

        # Setpoint support
        if controller.has_setpoint_control(ih_device_id):
            self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE

        # Setup swing lists
        if controller.has_vertical_swing():
            if hasattr(controller, "get_vertical_swing_list"):
                if swingmodes := controller.get_vertical_swing_list():
                    swingmode_list = []
                    for swingmode in swingmodes:
                        if swingmode in MAP_IH_TO_SWING:
                            swingmode_list.append(MAP_IH_TO_SWING[swingmode])
                        else:
                            _LOGGER.warning(
                                f"Unexpected vvane swingmode: {swingmode}, expected one of {MAP_IH_TO_SWING.keys()}"
                            )
                    self._swing_list.extend(swingmode_list)
            else:
                self._swing_list.extend(
                    ["Swing", "Position 1", "Position 2", "Position 3", "Position 4"]
                )
        if controller.has_horizontal_swing():
            if hasattr(controller, "get_horizontal_swing_list"):
                if swingmodes := controller.get_horizontal_swing_list():
                    swingmode_list = []
                    for swingmode in swingmodes:
                        if swingmode in MAP_IH_TO_SWING:
                            swingmode_list.append(MAP_IH_TO_HORIZONTAL_SWING[swingmode])
                        else:
                            _LOGGER.warning(
                                f"Unexpected hvane swingmode: {swingmode}, expected one of {MAP_IH_TO_SWING.keys()}"
                            )
                    self._swing_horizontal_list.extend(swingmode_list)
            else:
                self._swing_list.extend(
                    ["Swing", "Position 1", "Position 2", "Position 3", "Position 4"]
                )
        if len(self._swing_list) > 0:
            self._attr_supported_features |= ClimateEntityFeature.SWING_MODE
        if len(self._swing_horizontal_list) > 0:
            self._attr_supported_features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE

        # Setup fan speeds
        self._fan_modes = controller.get_fan_speed_list(ih_device_id)
        if self._fan_modes:
            self._attr_supported_features |= ClimateEntityFeature.FAN_MODE

        # Preset support
        if ih_device.get("climate_working_mode"):
            self._attr_supported_features |= ClimateEntityFeature.PRESET_MODE

        # Setup HVAC modes
        if modes := controller.get_mode_list(ih_device_id):
            mode_list = []
            for mode in modes:
                if mode in MAP_IH_TO_HVAC_MODE:
                    mode_list.append(MAP_IH_TO_HVAC_MODE[mode])
                else:
                    _LOGGER.warning("Unexpected mode: %s", mode)
            self._attr_hvac_modes.extend(mode_list)
        self._attr_hvac_modes.append(HVACMode.OFF)

    async def async_added_to_hass(self):
        """Subscribe to event updates."""
        _LOGGER.debug("Added climate device with state: %s", repr(self._ih_device))
        self._controller.add_update_callback(self.async_update_callback)

        try:
            await self._controller.connect()
        except IHConnectionError as ex:
            _LOGGER.error("Exception connecting to IntesisHome: %s", ex)
            raise PlatformNotReady from ex

    @property
    def name(self):
        """Return the name of the AC device."""
        return self._device_name

    @property
    def temperature_unit(self):
        """Intesishome API uses celsius on the backend."""
        return UnitOfTemperature.CELSIUS

    @property
    def extra_state_attributes(self):
        """Return the device specific state attributes."""
        attrs = {}
        if self._outdoor_temp is not None:
            attrs["outdoor_temp"] = self._outdoor_temp
        if self._power_consumption_heat:
            attrs["power_consumption_heat_kw"] = round(
                self._power_consumption_heat / 1000, 1
            )
        if self._power_consumption_cool:
            attrs["power_consumption_cool_kw"] = round(
                self._power_consumption_cool / 1000, 1
            )

        return attrs

    @property
    def unique_id(self):
        """Return unique ID for this device."""
        return self._device_id

    @property
    def target_temperature_step(self) -> float:
        """Return whether setpoint should be whole or half degree precision."""
        return self._setpoint_step

    @property
    def preset_modes(self):
        """Return a list of HVAC preset modes."""
        return self._preset_list

    @property
    def preset_mode(self):
        """Return the current preset mode."""
        return self._preset

    async def async_turn_on(self) -> None:
        """Turn device on."""
        self._power = True
        await self._controller.set_power_on()

    async def async_turn_off(self) -> None:
        """Turn device off."""
        self._power = False
        await self._controller.set_power_off()

    async def async_toggle(self) -> None:
        """Toggle device status."""
        if not self._controller.is_on():
            await self.async_turn_on()
        else:
            await self.async_turn_off()

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        if hvac_mode := kwargs.get(ATTR_HVAC_MODE):
            await self.async_set_hvac_mode(hvac_mode)

        if temperature := kwargs.get(ATTR_TEMPERATURE):
            _LOGGER.debug("Setting %s to %s degrees", self._device_type, temperature)
            await self._controller.set_temperature(self._device_id, temperature)
            self._target_temp = temperature

        # Write updated temperature to HA state to avoid flapping (API confirmation is slow)
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set operation mode."""
        _LOGGER.debug("Setting %s to %s mode", self._device_type, hvac_mode)
        if hvac_mode == HVACMode.OFF:
            self._power = False
            await self._controller.set_power_off()
            # Write changes to HA, API can be slow to push changes
            self.async_write_ha_state()
            return

        # First check device is turned on
        if not self._controller.is_on(self._device_id):
            self._power = True
            await self._controller.set_power_on()

        # Set the mode
        await self._controller.set_mode(self._device_id, MAP_HVAC_MODE_TO_IH[hvac_mode])

        # Send the temperature again in case changing modes has changed it
        if self._target_temp:
            await self._controller.set_temperature(self._device_id, self._target_temp)

        # Updates can take longer than 2 seconds, so update locally
        self._hvac_mode = hvac_mode
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode):
        """Set fan mode (from quiet, low, medium, high, auto)."""
        await self._controller.set_fan_speed(self._device_id, fan_mode)

        # Updates can take longer than 2 seconds, so update locally
        self._fan_speed = fan_mode
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode):
        """Set preset mode."""
        ih_preset_mode = MAP_PRESET_MODE_TO_IH.get(preset_mode)
        await self._controller.set_preset_mode(ih_preset_mode)

    async def async_set_swing_mode(self, swing_mode):
        """Set the vertical vane."""
        if swingmode := MAP_SWING_TO_IH.get(swing_mode):
            await self._controller.set_vertical_vane(self._device_id, swingmode)

    async def async_set_swing_horizontal_mode(self, swing_mode):
        """Set the horizontal vane."""
        if swingmode := MAP_HORIZONTAL_SWING_TO_IH.get(swing_mode):
            await self._controller.set_horizontal_vane(self._device_id, swingmode)

    async def async_update(self):
        """Copy values from controller dictionary to climate device."""
        # Update values from controller's device dictionary
        self._connected = self._controller.is_connected
        self._current_temp = self._controller.get_temperature(self._device_id)
        self._fan_speed = self._controller.get_fan_speed(self._device_id)
        self._power = self._controller.is_on(self._device_id)
        self._min_temp = self._controller.get_min_setpoint(self._device_id)
        self._max_temp = self._controller.get_max_setpoint(self._device_id)
        self._rssi = self._controller.get_rssi(self._device_id)
        self._run_hours = self._controller.get_run_hours(self._device_id)
        self._target_temp = self._controller.get_setpoint(self._device_id)
        self._outdoor_temp = self._controller.get_outdoor_temperature(self._device_id)

        # Operation mode
        mode = self._controller.get_mode(self._device_id)
        self._hvac_mode = MAP_IH_TO_HVAC_MODE.get(mode)

        # Preset mode
        preset = self._controller.get_preset_mode(self._device_id)
        self._preset = MAP_IH_TO_PRESET_MODE.get(preset)

        # Swing mode
        self._vvane = self._controller.get_vertical_swing(self._device_id)
        self._hvane = self._controller.get_horizontal_swing(self._device_id)

        # Power usage
        self._power_consumption_heat = self._controller.get_heat_power_consumption(
            self._device_id
        )
        self._power_consumption_cool = self._controller.get_cool_power_consumption(
            self._device_id
        )

        if not self._attr_supported_features:
            if self._fan_modes:
                self._attr_supported_features |= ClimateEntityFeature.FAN_MODE
            if self._controller.has_setpoint_control(self._device_id):
                self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE
            if len(self._swing_list) > 0:
                self._attr_supported_features |= ClimateEntityFeature.SWING_MODE
            if len(self._swing_horizontal_list) > 0:
                self._attr_supported_features |= (
                    ClimateEntityFeature.SWING_HORIZONTAL_MODE
                )
            if self._ih_device.get("climate_working_mode"):
                self._attr_supported_features |= ClimateEntityFeature.PRESET_MODE

    async def async_will_remove_from_hass(self):
        """Shutdown the controller when the device is being removed."""
        self._controller.remove_update_callback(self.async_update_callback)
        await self._controller.stop()
        self._controller = None

    @property
    def icon(self):
        """Return the icon for the current state."""
        icon = None
        if self._power:
            icon = MAP_STATE_ICONS.get(self._hvac_mode)
        return icon

    async def async_update_callback(self, device_id=None):
        """Let HA know there has been an update from the controller."""
        # Track changes in connection state
        if self._controller and not self._controller.is_connected and self._connected:
            # Connection has dropped
            self._connected = False
            reconnect_seconds = 30

            _LOGGER.info(
                "Connection to %s API was lost. Reconnecting in %i seconds",
                self._device_type,
                reconnect_seconds,
            )

            async def try_connect(retries):
                try:
                    await self._controller.connect()
                    _LOGGER.info("Reconnected to %s API", self._device_type)
                except IHConnectionError:
                    if retries < MAX_RETRIES:
                        wait_time = min(2**retries, MAX_WAIT_TIME)
                        _LOGGER.info(
                            "Failed to reconnect to %s API. Retrying in %i seconds",
                            self._device_type,
                            wait_time,
                        )
                        async_call_later(self.hass, wait_time, try_connect(retries + 1))
                    else:
                        _LOGGER.error(
                            "Failed to reconnect to %s API after %i retries. Giving up",
                            self._device_type,
                            MAX_RETRIES,
                        )

                async_call_later(self.hass, reconnect_seconds, try_connect(0))

        if self._controller.is_connected and not self._connected:
            # Connection has been restored
            self._connected = True
            _LOGGER.debug("Connection to %s API was restored", self._device_type)

        if not device_id or self._device_id == device_id:
            # Update all devices if no device_id was specified
            self.async_schedule_update_ha_state(True)

    @property
    def min_temp(self):
        """Return the minimum temperature for the current mode of operation."""
        return self._min_temp

    @property
    def max_temp(self):
        """Return the maximum temperature for the current mode of operation."""
        return self._max_temp

    @property
    def should_poll(self):
        """Poll for updates if pyIntesisHome doesn't have a socket open."""
        return True

    @property
    def fan_mode(self):
        """Return whether the fan is on."""
        return self._fan_speed

    @property
    def swing_mode(self):
        if self._vvane is None:
            return None
        swing = MAP_IH_TO_SWING.get(self._vvane)
        if swing is None:
            return None
        try:
            return swing
        except ValueError:
            return None

    @property
    def swing_horizontal_mode(self):
        if self._vvane is None:
            return None
        swing = MAP_IH_TO_SWING.get(self._hvane)
        if swing is None:
            return None
        try:
            return swing
        except ValueError:
            return None

    @property
    def fan_modes(self):
        """List of available fan modes."""
        return self._fan_modes

    @property
    def swing_modes(self):
        """List of available vertical swing positions."""
        return self._swing_list

    @property
    def swing_horizontal_modes(self):
        """List of available horizontal swing positions."""
        return self._swing_horizontal_list

    @property
    def available(self) -> bool:
        """If the device hasn't been able to connect, mark as unavailable."""
        return self._connected or self._connected is None

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        return self._current_temp

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current mode of operation if unit is on."""
        if self._power:
            return self._hvac_mode
        return HVACMode.OFF

    @property
    def target_temperature(self) -> float | None:
        """Return the current setpoint temperature if unit is on."""
        if self._power and self.hvac_mode not in [HVACMode.FAN_ONLY, HVACMode.OFF]:
            return self._target_temp
        return None

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        controller_model = None
        controller_fw_version = None
        if hasattr(self._controller, "get_model"):
            controller_model = self._controller.get_model()
        if hasattr(self._controller, "get_fw_version"):
            controller_fw_version = self._controller.get_fw_version()
        return DeviceInfo(
            identifiers={
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, self._controller.controller_id, self._device_id)
            },
            name=self._device_name,
            manufacturer=self._device_type.capitalize(),
            model=controller_model,
            sw_version=controller_fw_version,
        )
