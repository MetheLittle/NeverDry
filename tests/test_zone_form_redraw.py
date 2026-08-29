"""A rejected zone form must say why, and must not throw away what was typed.

GH #196: filling the zone form in, pressing submit, and watching every field
empty itself with no message on screen. Two independent faults met there —
an error filed against a field the frontend could not reach inside its
collapsed section, and a form redrawn from a schema that carried none of the
submitted values. Either one alone is survivable; together they make the
installation impossible to complete, because the one thing missing is never
named.
"""

from unittest.mock import MagicMock

import pytest
from never_dry import config_flow as cf
from never_dry.const import (
    CONF_ET_METHOD,
    CONF_RAIN_SENSOR,
    CONF_TEMP_SENSOR,
    CONF_ZONE_AREA,
    CONF_ZONE_EXPOSURE,
    CONF_ZONE_FLOW_METER_SENSOR,
    CONF_ZONE_FLOW_RATE,
    CONF_ZONE_MICROCLIMATE_FACTOR,
    CONF_ZONE_NAME,
    CONF_ZONE_VOLUME_ENTITY,
    CONF_ZONES,
    DELIVERY_MODE_FLOW_METER,
    DELIVERY_MODE_VOLUME_PRESET,
    EXPOSURE_CUSTOM,
    MAX_ZONE_NAME_LENGTH,
)


@pytest.fixture
def schema_calls(monkeypatch):
    """Record what the zone schema is asked to seed the redrawn form with."""
    calls: list[dict | None] = []

    def _schema(is_imperial, current=None):
        calls.append(current)
        return None

    monkeypatch.setattr(cf, "_zone_schema_initial", _schema)
    return calls


@pytest.fixture(autouse=True)
def _patch_flow_env(monkeypatch):
    """Fill the gaps in the conftest HA stubs, as the other flow tests do."""
    monkeypatch.setattr(cf, "_is_imperial", lambda hass: False)
    monkeypatch.setattr(cf.vol, "Schema", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(cf.vol, "Required", lambda *a, **k: object(), raising=False)
    monkeypatch.setattr(cf.vol, "Optional", lambda *a, **k: object(), raising=False)
    monkeypatch.setattr(cf, "_confirm_zone_schema", lambda: None)

    def _show_form(self, *, step_id, data_schema=None, errors=None, description_placeholders=None):
        return {"type": "form", "step_id": step_id, "errors": errors}

    def _create_entry(self, *, data=None, title=None):
        return {"type": "create_entry", "title": title, "data": data}

    for klass in (cf.NeverDryConfigFlow, cf.NeverDryOptionsFlow):
        monkeypatch.setattr(klass, "async_show_form", _show_form, raising=False)
        monkeypatch.setattr(klass, "async_create_entry", _create_entry, raising=False)


def _sectioned(**valve):
    """A zone as the frontend posts it: fields nested under their sections."""
    return {
        CONF_ZONE_NAME: "Prato",
        cf.SECTION_GROUND: {CONF_ZONE_AREA: 50.0},
        cf.SECTION_VALVE: {"valve": "switch.prato", "system_type": "sprinkler", **valve},
        cf.SECTION_SCHEDULING: {"threshold_mm": 10},
    }


class TestSuggestionRule:
    """The seeding rule itself, which decides whether a box comes back filled."""

    def test_first_render_carries_no_suggestion(self):
        # An explicit suggested_value=None here would blank the field's default.
        assert cf._suggest(None, CONF_ZONE_FLOW_RATE) == {}

    def test_submitted_value_is_offered_back(self):
        assert cf._suggest({CONF_ZONE_FLOW_RATE: 200.0}, CONF_ZONE_FLOW_RATE) == {
            "description": {"suggested_value": 200.0}
        }

    def test_emptied_box_stays_empty(self):
        # The field the error is about must not be refilled behind the user.
        assert cf._suggest({CONF_ZONE_NAME: "Prato"}, CONF_ZONE_FLOW_RATE) == {"description": {"suggested_value": None}}


class TestErrorsAreVisible:
    """Every field error reaches a key the frontend actually renders."""

    def test_sectioned_field_error_is_filed_three_ways(self):
        errors: dict[str, str] = {}
        cf._add_field_error(errors, CONF_ZONE_FLOW_RATE, "flow_rate_required")
        assert errors == {
            CONF_ZONE_FLOW_RATE: "flow_rate_required",
            f"{cf.SECTION_VALVE}.{CONF_ZONE_FLOW_RATE}": "flow_rate_required",
            "base": "flow_rate_required",
        }

    def test_top_level_field_needs_only_its_own_name(self):
        errors: dict[str, str] = {}
        cf._add_field_error(errors, CONF_ZONE_NAME, "zone_name_too_long")
        assert errors == {CONF_ZONE_NAME: "zone_name_too_long"}

    def test_first_error_owns_base(self):
        errors: dict[str, str] = {}
        cf._add_field_error(errors, CONF_ZONE_FLOW_RATE, "flow_rate_required")
        cf._add_field_error(errors, CONF_ZONE_MICROCLIMATE_FACTOR, "microclimate_factor_required")
        assert errors["base"] == "flow_rate_required"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("valve", "field"),
        [
            ({}, CONF_ZONE_FLOW_RATE),
            ({"delivery_mode": DELIVERY_MODE_FLOW_METER}, CONF_ZONE_FLOW_METER_SENSOR),
            ({"delivery_mode": DELIVERY_MODE_VOLUME_PRESET}, CONF_ZONE_VOLUME_ENTITY),
        ],
    )
    async def test_every_delivery_mode_rejection_is_announced(self, hass_mock, schema_calls, valve, field):
        flow = cf.NeverDryConfigFlow()
        flow.hass = hass_mock

        result = await flow.async_step_zone(_sectioned(**valve))

        assert result["step_id"] == "zone"
        # Without "base" the form comes back silent, which is GH #196.
        assert "base" in result["errors"]
        assert result["errors"][field] == result["errors"]["base"]


class TestRejectedInputSurvives:
    """A rejected form is redrawn from what was submitted, not from nothing."""

    @pytest.mark.asyncio
    async def test_zone_step_seeds_the_redraw(self, hass_mock, schema_calls):
        flow = cf.NeverDryConfigFlow()
        flow.hass = hass_mock

        await flow.async_step_zone(_sectioned())  # no flow rate: rejected

        assert schema_calls == [
            {
                CONF_ZONE_NAME: "Prato",
                CONF_ZONE_AREA: 50.0,
                "valve": "switch.prato",
                "system_type": "sprinkler",
                "threshold_mm": 10,
            }
        ]

    @pytest.mark.asyncio
    async def test_first_render_seeds_nothing(self, hass_mock, schema_calls):
        flow = cf.NeverDryConfigFlow()
        flow.hass = hass_mock

        await flow.async_step_zone()

        assert schema_calls == [None]

    @pytest.mark.asyncio
    async def test_overlong_name_keeps_the_rest_of_the_zone(self, hass_mock, schema_calls):
        flow = cf.NeverDryConfigFlow()
        flow.hass = hass_mock
        payload = _sectioned(flow_rate_lpm=200.0)
        payload[CONF_ZONE_NAME] = "x" * (MAX_ZONE_NAME_LENGTH + 1)

        await flow.async_step_zone(payload)

        assert schema_calls[0][CONF_ZONE_FLOW_RATE] == 200.0

    @pytest.mark.asyncio
    async def test_options_add_zone_seeds_form_units_not_metric(self, hass_mock, schema_calls):
        """The redraw must offer back the L/h the user typed, not the stored L/min.

        Seeding from the converted copy would divide the flow rate by 60 every
        time the form bounced, so a zone would quietly lose an order of
        magnitude while the user was busy fixing something else.
        """
        entry = MagicMock()
        entry.entry_id = "abc"
        entry.data = {CONF_ZONES: [{CONF_ZONE_NAME: "Prato"}]}
        options = cf.NeverDryOptionsFlow(entry)
        options.hass = hass_mock

        # Duplicate name: rejected, and the form comes back.
        await options.async_step_add_zone(_sectioned(flow_rate_lpm=600.0))

        assert schema_calls[0][CONF_ZONE_FLOW_RATE] == 600.0

    @pytest.mark.asyncio
    async def test_options_add_zone_override_error_seeds_the_redraw(self, hass_mock, schema_calls):
        entry = MagicMock()
        entry.entry_id = "abc"
        entry.data = {CONF_ZONES: []}
        options = cf.NeverDryOptionsFlow(entry)
        options.hass = hass_mock

        payload = _sectioned(flow_rate_lpm=600.0)
        payload[cf.SECTION_GROUND][CONF_ZONE_EXPOSURE] = EXPOSURE_CUSTOM
        payload[cf.SECTION_GROUND][CONF_ZONE_MICROCLIMATE_FACTOR] = None

        result = await options.async_step_add_zone(payload)

        assert result["errors"]["base"] == "microclimate_factor_required"
        assert schema_calls[0][CONF_ZONE_EXPOSURE] == EXPOSURE_CUSTOM


class TestSensorsStepSurvivesRejection:
    """The step before the zone form loses input the same way, and must not.

    Picking an ET method the declared sensors cannot support is the one way out
    of the first step that is not forward. The error itself is visible — that
    form has no sections — but the redraw used to arrive empty, so the two
    mandatory sensor pickers had to be found again before the method could even
    be corrected.
    """

    @pytest.fixture
    def sensors_schema_calls(self, monkeypatch):
        calls: list[dict | None] = []

        def _schema(is_imperial, current=None):
            calls.append(current)
            return None

        monkeypatch.setattr(cf, "_sensors_schema", _schema)
        return calls

    @pytest.mark.asyncio
    async def test_unsupported_method_keeps_the_declared_sensors(self, hass_mock, sensors_schema_calls):
        flow = cf.NeverDryConfigFlow()
        flow.hass = hass_mock
        submitted = {
            CONF_TEMP_SENSOR: "sensor.outdoor_temp",
            CONF_RAIN_SENSOR: "sensor.rain",
            # Penman-Monteith wants humidity, wind and radiation, none declared.
            CONF_ET_METHOD: "penman_monteith",
        }

        result = await flow.async_step_user(submitted)

        assert result["step_id"] == "user"
        assert result["errors"][CONF_ET_METHOD] == "et_method_missing_sensors"
        assert sensors_schema_calls == [submitted]

    @pytest.mark.asyncio
    async def test_first_render_seeds_nothing(self, hass_mock, sensors_schema_calls):
        flow = cf.NeverDryConfigFlow()
        flow.hass = hass_mock

        await flow.async_step_user()

        assert sensors_schema_calls == [None]
