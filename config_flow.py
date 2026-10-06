from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries

from .const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, DOMAIN


class EobywatelFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            return self.async_create_entry(
                title=url,
                data={**user_input, CONF_URL: url},
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL, default="https://ebok.zgkczernica.pl"): str,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
        )
