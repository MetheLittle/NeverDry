"""The edit form offers every field the add form does (GH #234).

A zone is added through ``_zone_schema_initial`` and edited through a schema
written out by hand inside ``async_step_edit_zone_detail``. The two drifted:
the wilting point joined the add form when Custom soil started asking for both
ends of the reservoir, and never reached the edit form. Validation is shared,
so editing a zone to Custom asked for a number the form had no box for, and
the zone could not be saved at all.

Nothing compared the two lists, which is how it shipped. The comparison below
reads both schemas with ``ast``, the same way ``test_zone_form_empty_fields``
reads the add form, so a field added to one form and forgotten in the other
fails here instead of in a tester's garden.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from never_dry import config_flow as cf
from never_dry.const import (
    CONF_ZONE_AREA,
    CONF_ZONE_FIELD_CAPACITY,
    CONF_ZONE_FLOW_RATE,
    CONF_ZONE_NAME,
    CONF_ZONE_SOIL_TYPE,
    CONF_ZONE_WILTING_POINT,
    CONF_ZONES,
    SOIL_TYPE_CUSTOM,
)

_SOURCE = Path(__file__).resolve().parent.parent / "custom_components" / "never_dry" / "config_flow.py"

# Fields the add form asks and the edit form deliberately does not. Empty today:
# an entry here is a decision, and it should say why next to it.
ADD_ONLY_BY_DESIGN: dict[str, str] = {}


def _declared_fields(function_name: str) -> set[str]:
    """Every constant passed as the key of a ``vol.Optional``/``vol.Required``."""
    tree = ast.parse(_SOURCE.read_text())
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function_name
    )
    fields = set()
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("Optional", "Required")
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            fields.add(node.args[0].id)
    return fields


class TestTheTwoFormsAskTheSameQuestions:
    def test_the_parser_sees_the_forms(self):
        """A parser that finds nothing would make the comparison pass for free."""
        assert len(_declared_fields("_zone_schema_initial")) > 10
        assert len(_declared_fields("async_step_edit_zone_detail")) > 10

    def test_every_add_field_is_in_the_edit_form(self):
        add = _declared_fields("_zone_schema_initial")
        edit = _declared_fields("async_step_edit_zone_detail")
        missing = add - edit - set(ADD_ONLY_BY_DESIGN)
        assert not missing, f"in the add form but not in the edit form: {sorted(missing)}"

    def test_every_edit_field_is_in_the_add_form(self):
        add = _declared_fields("_zone_schema_initial")
        edit = _declared_fields("async_step_edit_zone_detail")
        assert not edit - add, f"in the edit form but not in the add form: {sorted(edit - add)}"

    def test_the_exceptions_are_still_exceptions(self):
        """A stale entry would silently excuse a field that later goes missing."""
        edit = _declared_fields("async_step_edit_zone_detail")
        assert not set(ADD_ONLY_BY_DESIGN) & edit


# ── The case that was reported, driven through the step ──


def _entry(zones):
    entry = MagicMock()
    entry.entry_id = "abc"
    entry.data = {CONF_ZONES: zones}
    return entry


def _custom_soil_zone():
    return {
        CONF_ZONE_NAME: "Prato",
        CONF_ZONE_AREA: 20.0,
        CONF_ZONE_FLOW_RATE: 600.0,
        CONF_ZONE_SOIL_TYPE: SOIL_TYPE_CUSTOM,
        CONF_ZONE_FIELD_CAPACITY: 0.30,
        CONF_ZONE_WILTING_POINT: 0.10,
    }


@pytest.fixture
def optional_calls(monkeypatch):
    """Record every vol.Optional(...) the edit form declares."""
    calls: list[tuple[tuple, dict]] = []

    def _recorder(*args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(cf.vol, "Optional", _recorder, raising=False)
    return calls


@pytest.fixture(autouse=True)
def _patch_flow_env(monkeypatch):
    """Same stub set the other config-flow tests use."""
    monkeypatch.setattr(cf, "_is_imperial", lambda hass: False)
    monkeypatch.setattr(cf.vol, "Schema", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(cf.vol, "Required", lambda *a, **k: object(), raising=False)
    monkeypatch.setattr(cf.vol, "Optional", lambda *a, **k: object(), raising=False)
    monkeypatch.setattr(cf.vol, "UNDEFINED", object(), raising=False)
    monkeypatch.setattr(cf, "selector", MagicMock())
    monkeypatch.setattr(cf, "_confirm_zone_schema", lambda: None)
    monkeypatch.setattr(cf, "_zone_schema_initial", lambda imperial, current=None: None)

    def _show_form(self, *, step_id, data_schema=None, errors=None, description_placeholders=None):
        return {"type": "form", "step_id": step_id, "errors": errors}

    def _create_entry(self, *, data=None, title=None):
        return {"type": "create_entry", "title": title, "data": data}

    for klass in (cf.NeverDryConfigFlow, cf.NeverDryOptionsFlow):
        monkeypatch.setattr(klass, "async_show_form", _show_form, raising=False)
        monkeypatch.setattr(klass, "async_create_entry", _create_entry, raising=False)


def _flow(hass_mock, zone):
    flow = cf.NeverDryOptionsFlow(_entry([zone]))
    flow.hass = hass_mock
    flow._edit_zone_name = zone[CONF_ZONE_NAME]
    return flow


class TestEditingACustomSoil:
    @pytest.mark.asyncio
    async def test_the_wilting_point_has_a_box_and_shows_the_stored_value(self, hass_mock, optional_calls):
        await _flow(hass_mock, _custom_soil_zone()).async_step_edit_zone_detail()
        declared = {args[0]: kwargs for args, kwargs in optional_calls if args}

        assert CONF_ZONE_WILTING_POINT in declared, "the edit form has no box for the wilting point"
        assert "default" not in declared[CONF_ZONE_WILTING_POINT], "a default could never be cleared"
        assert declared[CONF_ZONE_WILTING_POINT]["description"]["suggested_value"] == 0.10

    @pytest.mark.asyncio
    async def test_both_ends_are_accepted_and_kept(self, hass_mock):
        """What the reporter tried: switch to Custom, give the numbers, save."""
        flow = _flow(hass_mock, {CONF_ZONE_NAME: "Prato", CONF_ZONE_AREA: 20.0, CONF_ZONE_FLOW_RATE: 600.0})

        result = await flow.async_step_edit_zone_detail(_custom_soil_zone())

        assert not result.get("errors"), result.get("errors")
        if result["type"] == "create_entry":
            zone = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"][CONF_ZONES][0]
        else:
            # Custom numbers with no probe to read them earn a warning first.
            assert result["step_id"] == "confirm_zone"
            zone = flow._pending_zone
        assert zone[CONF_ZONE_FIELD_CAPACITY] == 0.30
        assert zone[CONF_ZONE_WILTING_POINT] == 0.10

    @pytest.mark.asyncio
    async def test_a_missing_wilting_point_is_still_refused(self, hass_mock):
        """The box is new; the rule that asks for it is not, and stays."""
        flow = _flow(hass_mock, _custom_soil_zone())
        submitted = _custom_soil_zone()
        del submitted[CONF_ZONE_WILTING_POINT]

        result = await flow.async_step_edit_zone_detail(submitted)

        assert result["type"] == "form"
        assert CONF_ZONE_WILTING_POINT in str(result["errors"])
