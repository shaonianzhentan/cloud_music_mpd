from __future__ import annotations

from typing import Any
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow, ConfigEntry
from homeassistant.data_entry_flow import FlowResult
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.helpers.storage import STORAGE_DIR
from urllib.parse import quote
from homeassistant.core import callback

import os
from .manifest import manifest
from .utils import check_port

DOMAIN = manifest.domain
DEFAULT_NAME = manifest.name
DEFAULT_PORT = 6600

class SimpleConfigFlow(ConfigFlow, domain=DOMAIN):

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors = {}
        if user_input is not None:
            host = user_input.get(CONF_HOST)
            port = user_input.get(CONF_PORT)
            if check_port(host, port):
                return self.async_create_entry(title=DOMAIN, data=user_input)
            else:
                errors['base'] = 'login_failed'
        else:
            user_input = {}

        DATA_SCHEMA = vol.Schema({
            vol.Required(CONF_NAME, default=user_input.get(CONF_NAME, DEFAULT_NAME)): str,
            vol.Required(CONF_HOST, default=user_input.get(CONF_HOST)): str,
            vol.Required(CONF_PORT, default=user_input.get(CONF_PORT, DEFAULT_PORT)): int,
            vol.Optional(CONF_PASSWORD, default=user_input.get(CONF_PASSWORD, '')): str
        })
        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)