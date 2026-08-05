"""Tests for the per-zone site exposure (microclimate factor) config flow.

The exposure dropdown is a preset table, so the only input that can go
wrong is "Advanced (custom factor)" without a factor: it would resolve to a
neutral 1.0 at runtime and the zone would silently behave as if no
exposure had been selected. Every entry point into a zone form must reject
it (initial setup, add zone, edit zone).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from never_dry import config_flow as cf
from never_dry.const import (
    CONF_ZONE_AREA,
    CONF_ZONE_EXPOSURE,
    CONF_ZONE_FLOW_RATE,
    CONF_ZONE_MICROCLIMATE_FACTOR,
    CONF_ZONE_NAME,
    CONF_ZONES,
    DEFAULT_EXPOSURE,
    EXPOSURE_CUSTOM,
    EXPOSURE_DEEP_SHADE,
    EXPOSURE_MORNING_SUN,
    EXPOSURES,
    MICROCLIMATE_FACTOR_MAX,
    MICROCLIMATE_FACTOR_MIN,
)

_COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "never_dry"


def _entry(zones):
    entry = MagicMock()
    entry.entry_id = "abc"
    entry.data = {CONF_ZONES: zones}
    return entry


@pytest.fixture(autouse=True)
def _patch_flow_env(monkeypatch):
    """Same stub set as test_zone_config_guards, plus what the edit form needs."""
    monkeypatch.setattr(cf, "_is_imperial", lambda hass: False)
    monkeypatch.setattr(cf.vol, "Schema", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(cf.vol, "Required", lambda *a, **k: object(), raising=False)
    monkeypatch.setattr(cf.vol, "Optional", lambda *a, **k: object(), raising=False)
    monkeypatch.setattr(cf.vol, "UNDEFINED", object(), raising=False)
    # edit_zone_detail builds its schema inline, so every selector lookup
    # has to be answered.
    monkeypatch.setattr(cf, "selector", MagicMock())
    monkeypatch.setattr(cf, "_confirm_zone_schema", lambda: None)
    monkeypatch.setattr(cf, "_zone_schema_initial", lambda imperial: None)

    def _show_form(self, *, step_id, data_schema=None, errors=None, description_placeholders=None):
        return {
            "type": "form",
            "step_id": step_id,
            "errors": errors,
            "description_placeholders": description_placeholders,
        }

    def _create_entry(self, *, data=None, title=None):
        return {"type": "create_entry", "title": title, "data": data}

    for klass in (cf.NeverDryConfigFlow, cf.NeverDryOptionsFlow):
        monkeypatch.setattr(klass, "async_show_form", _show_form, raising=False)
        monkeypatch.setattr(klass, "async_create_entry", _create_entry, raising=False)


class TestExposureTable:
    """The preset table itself."""

    def test_default_exposure_is_neutral(self):
        assert EXPOSURES[DEFAULT_EXPOSURE]["factor"] == 1.0

    def test_custom_entry_has_no_preset_factor(self):
        assert EXPOSURES[EXPOSURE_CUSTOM]["factor"] is None

    def test_range_extends_above_one(self):
        """Paved and wall-adjacent zones genuinely exceed reference ET."""
        factors = [p["factor"] for p in EXPOSURES.values() if p["factor"] is not None]
        assert max(factors) > 1.0
        assert min(factors) > 0.0

    def test_presets_fit_the_custom_field_bounds(self):
        """A preset the custom field could not express would be inconsistent."""
        for key, preset in EXPOSURES.items():
            if preset["factor"] is not None:
                assert MICROCLIMATE_FACTOR_MIN <= preset["factor"] <= MICROCLIMATE_FACTOR_MAX, key

    def test_every_preset_has_a_translated_label(self):
        """``label`` is dev-facing: the dropdown text comes from the translations."""
        en = json.loads((_COMPONENT / "translations" / "en.json").read_text(encoding="utf-8"))
        assert set(EXPOSURES) == set(en["selector"]["exposure"]["options"])


class TestExposureErrors:
    """The pure validation helper."""

    def test_preset_is_clean(self):
        assert cf._exposure_errors({CONF_ZONE_EXPOSURE: EXPOSURE_DEEP_SHADE}) == {}

    def test_unset_exposure_is_clean(self):
        assert cf._exposure_errors({}) == {}

    def test_custom_without_factor_is_rejected(self):
        errors = cf._exposure_errors({CONF_ZONE_EXPOSURE: EXPOSURE_CUSTOM})
        assert errors == {CONF_ZONE_MICROCLIMATE_FACTOR: "microclimate_factor_required"}

    def test_custom_with_factor_is_clean(self):
        zone = {CONF_ZONE_EXPOSURE: EXPOSURE_CUSTOM, CONF_ZONE_MICROCLIMATE_FACTOR: 0.7}
        assert cf._exposure_errors(zone) == {}

    def test_preset_with_a_stale_factor_is_clean(self):
        """A leftover number is ignored at runtime, not an error."""
        zone = {CONF_ZONE_EXPOSURE: EXPOSURE_MORNING_SUN, CONF_ZONE_MICROCLIMATE_FACTOR: 0.7}
        assert cf._exposure_errors(zone) == {}


class TestInitialFlowExposure:
    """Initial setup: the zone step blocks on a factorless custom exposure."""

    @pytest.mark.asyncio
    async def test_custom_without_factor_shows_error(self, hass_mock):
        flow = cf.NeverDryConfigFlow()
        flow.hass = hass_mock

        result = await flow.async_step_zone(
            {
                CONF_ZONE_NAME: "Prato",
                CONF_ZONE_AREA: 20.0,
                CONF_ZONE_FLOW_RATE: 600.0,
                CONF_ZONE_EXPOSURE: EXPOSURE_CUSTOM,
            },
        )

        assert result["step_id"] == "zone"
        assert result["errors"] == {CONF_ZONE_MICROCLIMATE_FACTOR: "microclimate_factor_required"}
        assert flow._zones == []

    @pytest.mark.asyncio
    async def test_exposure_is_stored(self, hass_mock):
        flow = cf.NeverDryConfigFlow()
        flow.hass = hass_mock

        result = await flow.async_step_zone(
            {
                CONF_ZONE_NAME: "Prato",
                CONF_ZONE_AREA: 20.0,
                CONF_ZONE_FLOW_RATE: 600.0,
                CONF_ZONE_EXPOSURE: EXPOSURE_MORNING_SUN,
            },
        )

        assert result["step_id"] == "add_another"
        assert flow._zones[0][CONF_ZONE_EXPOSURE] == EXPOSURE_MORNING_SUN

    @pytest.mark.asyncio
    async def test_custom_with_factor_is_stored_unconverted(self, hass_mock):
        """The factor is dimensionless — no metric conversion may touch it."""
        flow = cf.NeverDryConfigFlow()
        flow.hass = hass_mock

        await flow.async_step_zone(
            {
                CONF_ZONE_NAME: "Prato",
                CONF_ZONE_AREA: 20.0,
                CONF_ZONE_FLOW_RATE: 600.0,
                CONF_ZONE_EXPOSURE: EXPOSURE_CUSTOM,
                CONF_ZONE_MICROCLIMATE_FACTOR: 0.65,
            },
        )

        assert flow._zones[0][CONF_ZONE_MICROCLIMATE_FACTOR] == 0.65


class TestOptionsFlowExposure:
    """add_zone / edit_zone_detail apply the same guard."""

    @pytest.mark.asyncio
    async def test_add_zone_custom_without_factor_shows_error(self, hass_mock):
        flow = cf.NeverDryOptionsFlow(_entry([]))
        flow.hass = hass_mock

        result = await flow.async_step_add_zone(
            {
                CONF_ZONE_NAME: "Prato",
                CONF_ZONE_AREA: 20.0,
                CONF_ZONE_FLOW_RATE: 600.0,
                CONF_ZONE_EXPOSURE: EXPOSURE_CUSTOM,
            },
        )

        assert result["step_id"] == "add_zone"
        assert result["errors"] == {CONF_ZONE_MICROCLIMATE_FACTOR: "microclimate_factor_required"}

    @pytest.mark.asyncio
    async def test_add_zone_with_preset_saves(self, hass_mock):
        entry = _entry([])
        flow = cf.NeverDryOptionsFlow(entry)
        flow.hass = hass_mock

        result = await flow.async_step_add_zone(
            {
                CONF_ZONE_NAME: "Prato",
                CONF_ZONE_AREA: 20.0,
                CONF_ZONE_FLOW_RATE: 600.0,
                CONF_ZONE_EXPOSURE: EXPOSURE_DEEP_SHADE,
            },
        )

        assert result["type"] == "create_entry"
        saved = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
        assert saved[CONF_ZONES][0][CONF_ZONE_EXPOSURE] == EXPOSURE_DEEP_SHADE

    @pytest.mark.asyncio
    async def test_edit_zone_custom_without_factor_shows_error(self, hass_mock):
        zone = {CONF_ZONE_NAME: "Prato", CONF_ZONE_AREA: 20.0, CONF_ZONE_FLOW_RATE: 10.0}
        flow = cf.NeverDryOptionsFlow(_entry([zone]))
        flow.hass = hass_mock
        flow._edit_zone_name = "Prato"

        result = await flow.async_step_edit_zone_detail(
            {
                CONF_ZONE_NAME: "Prato",
                CONF_ZONE_AREA: 20.0,
                CONF_ZONE_FLOW_RATE: 600.0,
                CONF_ZONE_EXPOSURE: EXPOSURE_CUSTOM,
            },
        )

        assert result["step_id"] == "edit_zone_detail"
        assert result["errors"] == {CONF_ZONE_MICROCLIMATE_FACTOR: "microclimate_factor_required"}
        flow.hass.config_entries.async_update_entry.assert_not_called()

    @pytest.mark.asyncio
    async def test_edit_zone_saves_exposure(self, hass_mock):
        zone = {CONF_ZONE_NAME: "Prato", CONF_ZONE_AREA: 20.0, CONF_ZONE_FLOW_RATE: 10.0}
        flow = cf.NeverDryOptionsFlow(_entry([zone]))
        flow.hass = hass_mock
        flow._edit_zone_name = "Prato"

        result = await flow.async_step_edit_zone_detail(
            {
                CONF_ZONE_NAME: "Prato",
                CONF_ZONE_AREA: 20.0,
                CONF_ZONE_FLOW_RATE: 600.0,
                CONF_ZONE_EXPOSURE: EXPOSURE_CUSTOM,
                CONF_ZONE_MICROCLIMATE_FACTOR: 1.2,
            },
        )

        assert result["type"] == "create_entry"
        saved = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
        edited = saved[CONF_ZONES][0]
        assert edited[CONF_ZONE_EXPOSURE] == EXPOSURE_CUSTOM
        assert edited[CONF_ZONE_MICROCLIMATE_FACTOR] == 1.2
