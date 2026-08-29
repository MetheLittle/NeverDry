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
    CONF_ZONE_AREA,
    CONF_ZONE_DELIVERY_MODE,
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
    monkeypatch.setattr(cf.vol, "UNDEFINED", object(), raising=False)
    # The edit form builds its schema inline, so the selector module has to
    # answer attribute access rather than be the empty stub conftest installs.
    monkeypatch.setattr(cf, "selector", MagicMock())
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


_STORED_ZONE = {
    CONF_ZONE_NAME: "Prato",
    CONF_ZONE_AREA: 50.0,
    CONF_ZONE_FLOW_RATE: 10.0,  # metric, L/min
    CONF_ZONE_DELIVERY_MODE: DELIVERY_MODE_FLOW_METER,
    CONF_ZONE_FLOW_METER_SENSOR: "sensor.prato_counter",
}


class TestDecliningTheSoftGuard:
    """Declining means "let me fix that", not "start again".

    A zone with unusual values routes through a confirmation step, and
    submitting it without ticking the box sends the user back to the form.
    That return redrew from the *stored* configuration — the very
    configuration the user was in the middle of changing — so a box they had
    deliberately emptied came back full and the edit was undone without a
    word. Reported from the field on 0.12.0-beta.3, on the design flow rate.

    Reported *after* the fix that made a cleared field survive a refusal, and
    it is worth saying why that fix did not cover this: a refusal redraws the
    same step with the input in hand, while declining leaves the step and
    comes back with nothing. Two paths back into one form.
    """

    def _options(self, hass):
        entry = MagicMock()
        entry.entry_id = "abc"
        entry.data = {CONF_ZONES: [dict(_STORED_ZONE)]}
        options = cf.NeverDryOptionsFlow(entry)
        options.hass = hass
        options._edit_zone_name = "Prato"
        return options

    def test_the_form_opens_on_what_is_saved(self, hass_mock):
        options = self._options(hass_mock)

        assert options._edit_zone_seed() == _STORED_ZONE

    def test_a_declined_submission_wins_over_what_is_saved(self, hass_mock):
        options = self._options(hass_mock)
        # The rate is gone from the submission; the stored zone still has it.
        options._pending_zone = {CONF_ZONE_NAME: "Prato", CONF_ZONE_AREA: 50.0}

        seed = options._edit_zone_seed()

        assert CONF_ZONE_FLOW_RATE not in seed
        assert seed[CONF_ZONE_AREA] == 50.0

    def test_the_declined_submission_is_consumed_once(self, hass_mock):
        """It seeds the form it was declined from, and then it is gone."""
        options = self._options(hass_mock)
        options._pending_zone = {CONF_ZONE_NAME: "Prato"}

        options._edit_zone_seed()

        assert options._edit_zone_seed() == _STORED_ZONE

    @pytest.mark.asyncio
    async def test_declining_carries_the_submission_back(self, hass_mock):
        options = self._options(hass_mock)
        submitted = {
            CONF_ZONE_NAME: "Prato",
            cf.SECTION_GROUND: {CONF_ZONE_AREA: 50.0},
            cf.SECTION_VALVE: {
                "valve": "switch.prato",
                CONF_ZONE_DELIVERY_MODE: DELIVERY_MODE_FLOW_METER,
                CONF_ZONE_FLOW_METER_SENSOR: "sensor.prato_counter",
                "system_type": "sprinkler",
            },
            cf.SECTION_SCHEDULING: {},
        }
        # The meter is declared, so a missing design rate is a warning here,
        # not a refusal — which is the path that leads to the guard at all.
        result = await options.async_step_edit_zone_detail(submitted)
        assert result["step_id"] == "confirm_zone"

        options._pending_action = "edit"
        await options.async_step_confirm_zone({"confirm": False})

        # Consumed by the redraw on the way back, and the rate stayed gone.
        assert options._pending_zone is None

    @pytest.mark.asyncio
    async def test_confirming_saves_and_forgets(self, hass_mock):
        """A kept submission must not leak into the next visit to the form."""
        options = self._options(hass_mock)
        options._pending_zone = {CONF_ZONE_NAME: "Prato"}
        options._pending_form = {CONF_ZONE_NAME: "Prato"}
        options._pending_action = "edit"

        await options.async_step_confirm_zone({"confirm": True})

        assert options._pending_zone is None
        assert options._pending_form is None
        assert options._edit_zone_seed() == _STORED_ZONE
