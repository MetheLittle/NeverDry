"""One test per zone-form field left empty, at each of the three doors.

GH #196 was not really about the design flow rate. It was about what happens
when *any* field is left empty: whether the flow answers, and whether the
answer costs the user everything else they typed. So the matrix is built by
reading ``_zone_schema_initial`` with ``ast`` — the same static-parsing trick
``test_translation_consistency`` uses — rather than by listing fields here.
A field added to the form and forgotten here fails the coverage test instead
of quietly going untested.

A zone can be submitted from three places — first-run setup, *add zone* and
*edit zone* in the options — and they used to answer differently to the same
omission: setup refused, the other two saved and said nothing. Every case here
runs at all three, and one test asserts directly that the three answers agree.

Two mechanisms protect a field, and each gets the assertion that fits it:

* A ``vol.Required`` field is guarded by the form. Home Assistant validates
  user input against the schema before the step ever runs, so an empty one
  cannot reach the handler — the test asserts the field is declared that way,
  because that declaration *is* the guard.
* A ``vol.Optional`` field can arrive missing, so the step is actually driven
  with it dropped. Whatever the flow decides, it must decide out loud: either
  it accepts the zone, or it refuses with an error the frontend can render —
  and either way the rest of the submission survives.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from never_dry import config_flow as cf
from never_dry import const
from never_dry.const import (
    CONF_ZONE_AREA,
    CONF_ZONE_DELIVERY_MODE,
    CONF_ZONE_DELIVERY_TIMEOUT,
    CONF_ZONE_EFFICIENCY,
    CONF_ZONE_EXPOSURE,
    CONF_ZONE_FLOW_METER_SENSOR,
    CONF_ZONE_FLOW_RATE,
    CONF_ZONE_IRRIGATION_MODE,
    CONF_ZONE_IRRIGATION_TIME,
    CONF_ZONE_KC,
    CONF_ZONE_MICROCLIMATE_FACTOR,
    CONF_ZONE_NAME,
    CONF_ZONE_PLANT_FAMILY,
    CONF_ZONE_SYSTEM_TYPE,
    CONF_ZONE_THRESHOLD,
    CONF_ZONE_VALVE,
    CONF_ZONE_VOLUME_ENTITY,
    CONF_ZONE_VWC_SENSOR,
    CONF_ZONES,
    DELIVERY_MODE_ESTIMATED_FLOW,
    DELIVERY_MODE_FLOW_METER,
    DELIVERY_MODE_VOLUME_PRESET,
    EXPOSURE_CUSTOM,
    IRRIGATION_MODE_SCHEDULED,
    PLANT_FAMILY_CUSTOM,
    SYSTEM_TYPE_CUSTOM,
)

_CONFIG_FLOW = Path(__file__).resolve().parent.parent / "custom_components" / "never_dry" / "config_flow.py"


# ── reading the form out of the source ────────────────────────────────


def _resolve(name: str) -> str:
    """A field name written as a constant, resolved to the key it stands for."""
    for module in (const, cf):
        if hasattr(module, name):
            return getattr(module, name)
    raise AssertionError(f"cannot resolve {name}")


def _marker(node: ast.AST) -> tuple[str, bool] | None:
    """``vol.Required(FOO)``/``vol.Optional(FOO)`` -> (field, required)."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr not in ("Required", "Optional") or not node.args:
        return None
    first = node.args[0]
    if not isinstance(first, ast.Name):
        return None
    return _resolve(first.id), node.func.attr == "Required"


def _dict_of_schema(node: ast.AST) -> ast.Dict | None:
    """The mapping inside a ``vol.Schema({...})`` call."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Schema"
        and node.args
        and isinstance(node.args[0], ast.Dict)
    ):
        return node.args[0]
    return None


def _zone_form() -> dict[str, tuple[str | None, bool]]:
    """Read the zone form: ``{field: (section or None, required)}``."""
    tree = ast.parse(_CONFIG_FLOW.read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_zone_schema_initial")
    returned = next(n.value for n in ast.walk(fn) if isinstance(n, ast.Return))
    top = _dict_of_schema(returned)
    assert top is not None, "the zone form is no longer a vol.Schema({...}) literal"

    fields: dict[str, tuple[str | None, bool]] = {}
    for key, value in zip(top.keys, top.values, strict=True):
        marked = _marker(key)
        assert marked is not None, ast.dump(key)
        name, required = marked
        # A section() wraps an inner schema; its key is the section name.
        if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "section":
            inner = _dict_of_schema(value.args[0])
            assert inner is not None, f"section {name} no longer wraps a vol.Schema"
            for k2, _v2 in zip(inner.keys, inner.values, strict=True):
                m2 = _marker(k2)
                assert m2 is not None, ast.dump(k2)
                fields[m2[0]] = (name, m2[1])
        else:
            fields[name] = (None, required)
    return fields


ZONE_FORM = _zone_form()


# ── a zone with every box filled, and filled coherently ───────────────

# Every preset sits on Custom with its box supplied, so no value in here is
# dead weight: dropping one is always a real subtraction, never the removal
# of something that was being ignored anyway.
FILLED = {
    CONF_ZONE_NAME: "Prato",
    CONF_ZONE_AREA: 50.0,
    CONF_ZONE_PLANT_FAMILY: PLANT_FAMILY_CUSTOM,
    CONF_ZONE_KC: 0.8,
    CONF_ZONE_VWC_SENSOR: "sensor.prato_moisture",
    CONF_ZONE_EXPOSURE: EXPOSURE_CUSTOM,
    CONF_ZONE_MICROCLIMATE_FACTOR: 1.1,
    CONF_ZONE_VALVE: "switch.prato",
    CONF_ZONE_DELIVERY_MODE: DELIVERY_MODE_ESTIMATED_FLOW,
    CONF_ZONE_FLOW_RATE: 600.0,  # L/h — 10 L/min, inside the plausible band
    CONF_ZONE_FLOW_METER_SENSOR: "sensor.prato_counter",
    CONF_ZONE_VOLUME_ENTITY: "number.prato_volume",
    CONF_ZONE_SYSTEM_TYPE: SYSTEM_TYPE_CUSTOM,
    CONF_ZONE_EFFICIENCY: 0.85,
    CONF_ZONE_DELIVERY_TIMEOUT: 1800,
    CONF_ZONE_IRRIGATION_MODE: IRRIGATION_MODE_SCHEDULED,
    CONF_ZONE_IRRIGATION_TIME: "06:00:00",
    CONF_ZONE_THRESHOLD: 10.0,
}

# Dropping these is refused. Each is a value nothing else can supply: a
# preset sitting on Custom with an empty box means nothing at all, and
# without a design flow rate a volume never becomes a duration.
REFUSED_WHEN_EMPTY = {
    CONF_ZONE_KC: "kc_required",
    CONF_ZONE_MICROCLIMATE_FACTOR: "microclimate_factor_required",
    CONF_ZONE_EFFICIENCY: "efficiency_required",
    CONF_ZONE_FLOW_RATE: "flow_rate_required",
}


def _payload(without: str | None = None) -> dict:
    """The filled zone as the frontend posts it, optionally minus one field."""
    body: dict = {}
    for field, value in FILLED.items():
        if field == without:
            continue
        section = ZONE_FORM[field][0]
        if section is None:
            body[field] = value
        else:
            body.setdefault(section, {})[field] = value
    return body


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


@pytest.fixture
def seeded(monkeypatch):
    """What a redrawn ``_zone_schema_initial`` is asked to offer back."""
    calls: list[dict | None] = []

    def _schema(is_imperial, current=None):
        calls.append(current)
        return None

    monkeypatch.setattr(cf, "_zone_schema_initial", _schema)
    return calls


# ── the three doors a zone can be submitted through ───────────────────

DOORS = ("setup", "add", "edit")

# Each door redraws its own step when it refuses, so this is how "refused"
# is recognised without asking the flow to tell us.
_REFUSAL_STEP = {"setup": "zone", "add": "add_zone", "edit": "edit_zone_detail"}

# Only these two build the shared initial schema; the edit form builds its own
# inline, and its seeding is pinned by test_zone_override_clearing.
_SEEDS_SHARED_SCHEMA = ("setup", "add")


async def _submit(door: str, hass, payload: dict):
    """Post a zone at one door and hand back (result, flow)."""
    if door == "setup":
        flow = cf.NeverDryConfigFlow()
        flow.hass = hass
        return await flow.async_step_zone(payload), flow

    entry = MagicMock()
    entry.entry_id = "abc"
    if door == "add":
        entry.data = {CONF_ZONES: []}
        flow = cf.NeverDryOptionsFlow(entry)
        flow.hass = hass
        return await flow.async_step_add_zone(payload), flow

    # Editing a zone that is already stored, under the name being submitted.
    entry.data = {CONF_ZONES: [{CONF_ZONE_NAME: FILLED[CONF_ZONE_NAME]}]}
    flow = cf.NeverDryOptionsFlow(entry)
    flow.hass = hass
    flow._edit_zone_name = FILLED[CONF_ZONE_NAME]
    return await flow.async_step_edit_zone_detail(payload), flow


def _was_refused(door: str, result: dict) -> bool:
    return result.get("step_id") == _REFUSAL_STEP[door]


class TestCoverage:
    """The matrix has to keep up with the form on its own."""

    def test_every_field_has_a_value_to_drop(self):
        assert set(ZONE_FORM) == set(FILLED), (
            "the zone form and this matrix have drifted; give the new field a "
            "plausible value in FILLED so leaving it empty gets tested"
        )

    def test_the_form_still_has_its_three_sections(self):
        assert {s for s, _ in ZONE_FORM.values() if s} == {
            cf.SECTION_GROUND,
            cf.SECTION_VALVE,
            cf.SECTION_SCHEDULING,
        }


class TestFilledZoneIsAccepted:
    """The baseline the matrix subtracts from must itself go through."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door", DOORS)
    async def test_nothing_missing_nothing_refused(self, hass_mock, seeded, door):
        result, _flow = await _submit(door, hass_mock, _payload())

        assert not _was_refused(door, result)


class TestRequiredFieldsAreGuardedByTheForm:
    """An empty required field cannot reach the step, and that is the point."""

    @pytest.mark.parametrize("field", sorted(f for f, (_, req) in ZONE_FORM.items() if req))
    def test_declared_required(self, field):
        # Home Assistant validates against the schema before calling the step,
        # so this declaration is what stops an empty submission — not a check
        # inside the handler, which would never run.
        assert ZONE_FORM[field][1] is True


class TestOptionalFieldLeftEmpty:
    """Every optional field, dropped one at a time, at each door."""

    OPTIONAL = sorted(f for f, (_, req) in ZONE_FORM.items() if not req)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door", DOORS)
    @pytest.mark.parametrize("field", OPTIONAL)
    async def test_the_answer_is_never_silent(self, hass_mock, seeded, door, field):
        result, _flow = await _submit(door, hass_mock, _payload(without=field))

        if not _was_refused(door, result):
            return
        # "base" is the one key the frontend renders whatever section the
        # field sits in, so it decides whether the user is told at all.
        assert result["errors"], f"{door}/{field}: refused with no error at all"
        assert "base" in result["errors"], f"{door}/{field}: refused with nothing the form can show"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door", DOORS)
    @pytest.mark.parametrize("field", OPTIONAL)
    async def test_a_refused_zone_is_not_saved(self, hass_mock, seeded, door, field):
        result, flow = await _submit(door, hass_mock, _payload(without=field))

        if not _was_refused(door, result):
            return
        if door == "setup":
            assert flow._zones == []
        else:
            flow.hass.config_entries.async_update_entry.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door", _SEEDS_SHARED_SCHEMA)
    @pytest.mark.parametrize("field", OPTIONAL)
    async def test_nothing_else_is_lost(self, hass_mock, seeded, door, field):
        result, _flow = await _submit(door, hass_mock, _payload(without=field))

        if not _was_refused(door, result):
            return
        assert seeded, f"{door}/{field}: the form was redrawn from nothing"
        offered = seeded[-1]
        for kept, value in FILLED.items():
            if kept == field:
                assert offered.get(kept) is None, f"{door}/{field}: came back filled in behind the user"
            else:
                assert offered.get(kept) == value, f"{door}/{field}: dropping it also lost {kept}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door", DOORS)
    @pytest.mark.parametrize(("field", "code"), sorted(REFUSED_WHEN_EMPTY.items()))
    async def test_the_refusal_names_the_field(self, hass_mock, seeded, door, field, code):
        result, _flow = await _submit(door, hass_mock, _payload(without=field))

        assert _was_refused(door, result), f"{door}: {field} was accepted empty"
        assert result["errors"][field] == code
        assert result["errors"][f"{ZONE_FORM[field][0]}.{field}"] == code
        assert result["errors"]["base"] == code

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", OPTIONAL)
    async def test_the_three_doors_agree(self, hass_mock, seeded, field):
        """The asymmetry this file exists to keep closed.

        Setup used to refuse a zone with no design flow rate while *add zone*
        and *edit zone* saved it and said nothing — the same omission with
        three different answers, two of them silent. A rule enforced at one
        door and forgotten at the next fails here.
        """
        answers = {}
        for door in DOORS:
            result, _flow = await _submit(door, hass_mock, _payload(without=field))
            answers[door] = _was_refused(door, result)

        assert len(set(answers.values())) == 1, f"{field}: the doors disagree — {answers}"
        assert answers["setup"] is (field in REFUSED_WHEN_EMPTY), (
            f"{field}: refused={answers['setup']}, but REFUSED_WHEN_EMPTY says otherwise"
        )


class TestModeWithoutItsSensor:
    """The other half of the asymmetry: a mode is refused, not quietly swapped.

    ``_coerce_delivery_mode`` used to downgrade *flow meter* or *volume preset*
    to *estimated flow* whenever the entity that mode depends on was missing —
    only in the two options steps, and without a word. Two things were wrong
    with it. The user picked a mode and got another one; and the mode it landed
    on needs a design flow rate that nothing then checked, so the downgrade
    could manufacture the very zone this file is about. Its commit message said
    it was there for an entity *removed* from Home Assistant, but it only ever
    ran on form submission — nobody reopens the form when an entity vanishes,
    so it never did that job either.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door", DOORS)
    @pytest.mark.parametrize(
        ("mode", "sensor_field", "code"),
        [
            (DELIVERY_MODE_FLOW_METER, CONF_ZONE_FLOW_METER_SENSOR, "flow_meter_required"),
            (DELIVERY_MODE_VOLUME_PRESET, CONF_ZONE_VOLUME_ENTITY, "volume_entity_required"),
        ],
    )
    async def test_refused_at_every_door(self, hass_mock, seeded, door, mode, sensor_field, code):
        payload = _payload(without=sensor_field)
        payload[ZONE_FORM[CONF_ZONE_DELIVERY_MODE][0]][CONF_ZONE_DELIVERY_MODE] = mode

        result, _flow = await _submit(door, hass_mock, payload)

        assert _was_refused(door, result), f"{door}: {mode} accepted with no {sensor_field}"
        assert result["errors"][sensor_field] == code
        assert result["errors"]["base"] == code

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door", DOORS)
    async def test_the_chosen_mode_is_never_swapped(self, hass_mock, seeded, door):
        """A complete flow-meter zone keeps the mode it was given."""
        payload = _payload()
        payload[ZONE_FORM[CONF_ZONE_DELIVERY_MODE][0]][CONF_ZONE_DELIVERY_MODE] = DELIVERY_MODE_FLOW_METER

        result, flow = await _submit(door, hass_mock, payload)

        assert not _was_refused(door, result)
        saved = flow._zones[0] if door == "setup" else _saved_zone(flow)
        assert saved[CONF_ZONE_DELIVERY_MODE] == DELIVERY_MODE_FLOW_METER


def _saved_zone(flow) -> dict:
    """The zone an options step handed to async_update_entry."""
    call = flow.hass.config_entries.async_update_entry.call_args
    assert call is not None, "the zone was never saved"
    return call.kwargs["data"][CONF_ZONES][-1]
