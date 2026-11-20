"""Einrichtung über die UI."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .const import (
    BACKEND_CS3,
    BACKEND_CS4,
    BACKEND_NAMES,
    BACKEND_TTN,
    CONF_API_KEY,
    CONF_API_TOKEN,
    CONF_API_URL,
    CONF_APP_ID,
    CONF_BACKEND,
    CONF_FIRE_EVENTS,
    CONF_IMPORT,
    CONF_TENANT,
    CONF_TLS,
    CONF_TOPIC,
    DEFAULT_TTN_HOST,
    DEFAULT_TTN_TENANT,
    DOMAIN,
    default_topic,
)
from .hub import ImportError_, fetch_devices, test_mqtt_connection

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class LoRaWANConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=[BACKEND_TTN, BACKEND_CS4, BACKEND_CS3])

    # ----------------------------------------------------------------- TTN

    async def async_step_ttn(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            tenant = user_input.get(CONF_TENANT) or DEFAULT_TTN_TENANT
            data = {
                CONF_BACKEND: BACKEND_TTN,
                CONF_HOST: user_input[CONF_HOST].strip(),
                CONF_PORT: 8883,
                CONF_TLS: True,
                CONF_APP_ID: user_input[CONF_APP_ID].strip(),
                CONF_TENANT: tenant,
                CONF_API_KEY: user_input[CONF_API_KEY].strip(),
                CONF_USERNAME: f"{user_input[CONF_APP_ID].strip()}@{tenant}",
                CONF_PASSWORD: user_input[CONF_API_KEY].strip(),
                CONF_IMPORT: user_input[CONF_IMPORT],
            }
            await self.async_set_unique_id(f"ttn_{data[CONF_HOST]}_{data[CONF_APP_ID]}")
            self._abort_if_unique_id_configured()
            errors = await self._validate(data)
            if not errors:
                return self.async_create_entry(title=f"TTN: {data[CONF_APP_ID]}", data=data)

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=DEFAULT_TTN_HOST): str,
                vol.Required(CONF_APP_ID): str,
                vol.Required(CONF_API_KEY): PASSWORD,
                vol.Optional(CONF_TENANT, default=DEFAULT_TTN_TENANT): str,
                vol.Required(CONF_IMPORT, default=True): bool,
            }
        )
        return self.async_show_form(
            step_id="ttn", data_schema=self.add_suggested_values_to_schema(schema, user_input), errors=errors
        )

    # ----------------------------------------------------------------- ChirpStack

    async def async_step_chirpstack_v4(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._chirpstack(BACKEND_CS4, user_input)

    async def async_step_chirpstack_v3(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._chirpstack(BACKEND_CS3, user_input)

    async def _chirpstack(self, backend: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {CONF_BACKEND: backend, **{k: (v.strip() if isinstance(v, str) else v) for k, v in user_input.items()}}
            if data.get(CONF_IMPORT) and not (data.get(CONF_API_URL) and data.get(CONF_API_TOKEN) and data.get(CONF_APP_ID)):
                errors["base"] = "import_needs_api"
            else:
                await self.async_set_unique_id(f"{backend}_{data[CONF_HOST]}_{data.get(CONF_APP_ID) or 'all'}")
                self._abort_if_unique_id_configured()
                errors = await self._validate(data)
                if not errors:
                    title = f"{BACKEND_NAMES[backend]}: {data.get(CONF_APP_ID) or data[CONF_HOST]}"
                    return self.async_create_entry(title=title, data=data)

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=1883): vol.Coerce(int),
                vol.Required(CONF_TLS, default=False): bool,
                vol.Optional(CONF_USERNAME): str,
                vol.Optional(CONF_PASSWORD): PASSWORD,
                vol.Optional(CONF_APP_ID): str,
                vol.Required(CONF_IMPORT, default=False): bool,
                vol.Optional(CONF_API_URL): str,
                vol.Optional(CONF_API_TOKEN): PASSWORD,
            }
        )
        return self.async_show_form(
            step_id=backend, data_schema=self.add_suggested_values_to_schema(schema, user_input), errors=errors
        )

    # ----------------------------------------------------------------- shared

    async def _validate(self, data: dict[str, Any]) -> dict[str, str]:
        err = await self.hass.async_add_executor_job(test_mqtt_connection, data)
        if err:
            return {"base": err}
        if data.get(CONF_IMPORT):
            try:
                await fetch_devices(self.hass, data)
            except ImportError_ as e:
                return {"base": f"api_{e.reason}"}
        return {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return LoRaWANOptionsFlow()


class LoRaWANOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if not user_input.get(CONF_TOPIC):
                user_input[CONF_TOPIC] = ""
            return self.async_create_entry(data=user_input)

        cfg = {**self.config_entry.data, **self.config_entry.options}
        topic_default = default_topic(cfg[CONF_BACKEND], cfg.get(CONF_APP_ID), cfg.get(CONF_TENANT))
        schema = vol.Schema(
            {
                vol.Optional(CONF_TOPIC, description={"suggested_value": cfg.get(CONF_TOPIC) or topic_default}): str,
                vol.Required(CONF_FIRE_EVENTS, default=cfg.get(CONF_FIRE_EVENTS, True)): bool,
                vol.Required(CONF_IMPORT, default=cfg.get(CONF_IMPORT, False)): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
