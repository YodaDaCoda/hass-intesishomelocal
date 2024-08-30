"""Config flow for IntesisHome Local."""

import logging

from pyintesishome import IHAuthenticationError, IHConnectionError, IntesisHomeLocal
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Define the schema as a static variable
LOCAL_AUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class IntesisConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for IntesisHome Local."""

    VERSION = 1

    def __init__(self):
        """Initialize."""
        self._data = {}

    def _show_setup_form(self, errors=None):
        return self.async_show_form(
            step_id="user", data_schema=LOCAL_AUTH_SCHEMA, errors=errors or {}
        )

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input:
            self._data.update(user_input)
            return await self.async_test_config()

        return self._show_setup_form(errors)

    async def async_test_config(self) -> FlowResult:
        """Handle the device connection step."""
        errors: dict[str, str] = {}
        controller: IntesisHomeLocal = None

        controller = IntesisHomeLocal(
            self._data[CONF_HOST],
            self._data[CONF_USERNAME],
            self._data[CONF_PASSWORD],
            loop=self.hass.loop,
            websession=async_get_clientsession(self.hass),
        )

        try:
            await controller.poll_status()
        except IHAuthenticationError:
            errors["base"] = "invalid_auth"
            return self._show_setup_form(errors)
        except IHConnectionError:
            errors[CONF_HOST] = "cannot_connect"
            return self._show_setup_form(errors)
        except Exception as e:
            _LOGGER.exception("Unexpected exception: %s", e, exc_info=True)
            errors["base"] = "unknown"
            return self._show_setup_form(errors)

        unique_id = f"{controller.device_type}_{controller.controller_id}".lower()

        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        # Pass the controller through to the platform setup
        self.hass.data.setdefault(DOMAIN, {})
        self.hass.data[DOMAIN].setdefault("controller", {})
        self.hass.data[DOMAIN]["controller"][unique_id] = controller

        name = f"{controller.device_type} {controller.name}"

        return self.async_create_entry(
            title=name,
            data=self._data,
        )

    async def async_step_import(self, import_data) -> FlowResult:
        """Handle configuration by yaml file."""
        return await self.async_step_user(import_data)
