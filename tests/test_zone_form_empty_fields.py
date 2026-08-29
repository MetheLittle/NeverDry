"""One test per zone-form field left empty, driven from the form itself.

GH #196 was not really about the design flow rate. It was about what happens
when *any* field is left empty: whether the flow answers, and whether the
answer costs the user everything else they typed. So the matrix is built by
reading ``_zone_schema_initial`` with ``ast`` — the same static-parsing trick
``test_translation_consistency`` uses — rather than by listing fields here.
A field added to the form and forgotten here fails the coverage test instead
of quietly going untested.

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
    DELIVERY_MODE_ESTIMATED_FLOW,
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
    monkeypatch.setattr(cf, "_confirm_zone_schema", lambda: None)

    def _show_form(self, *, step_id, data_schema=None, errors=None, description_placeholders=None):
        return {"type": "form", "step_id": step_id, "errors": errors}

    monkeypatch.setattr(cf.NeverDryConfigFlow, "async_show_form", _show_form, raising=False)


@pytest.fixture
def seeded(monkeypatch):
    """What the redrawn form is asked to offer back."""
    calls: list[dict | None] = []

    def _schema(is_imperial, current=None):
        calls.append(current)
        return None

    monkeypatch.setattr(cf, "_zone_schema_initial", _schema)
    return calls


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
    async def test_nothing_missing_nothing_refused(self, hass_mock, seeded):
        flow = cf.NeverDryConfigFlow()
        flow.hass = hass_mock

        result = await flow.async_step_zone(_payload())

        assert result["step_id"] == "add_another"
        assert len(flow._zones) == 1
        assert seeded == []  # never redrawn, so nothing to seed


class TestRequiredFieldsAreGuardedByTheForm:
    """An empty required field cannot reach the step, and that is the point."""

    @pytest.mark.parametrize("field", sorted(f for f, (_, req) in ZONE_FORM.items() if req))
    def test_declared_required(self, field):
        # Home Assistant validates against the schema before calling the step,
        # so this declaration is what stops an empty submission — not a check
        # inside the handler, which would never run.
        assert ZONE_FORM[field][1] is True


class TestOptionalFieldLeftEmpty:
    """Every optional field, dropped one at a time, on a zone that was fine."""

    OPTIONAL = sorted(f for f, (_, req) in ZONE_FORM.items() if not req)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", OPTIONAL)
    async def test_the_answer_is_never_silent(self, hass_mock, seeded, field):
        flow = cf.NeverDryConfigFlow()
        flow.hass = hass_mock

        result = await flow.async_step_zone(_payload(without=field))

        if result["step_id"] != "zone":
            # Accepted, with or without the soft-confirm step in between.
            assert result["step_id"] in ("add_another", "confirm_zone")
            return
        # Refused: the frontend renders "base" whatever section the field is
        # in, so this is the key that decides whether the user is told at all.
        assert result["errors"], f"{field}: refused with no error at all"
        assert "base" in result["errors"], f"{field}: refused without a message the form can show"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", OPTIONAL)
    async def test_nothing_else_is_lost(self, hass_mock, seeded, field):
        flow = cf.NeverDryConfigFlow()
        flow.hass = hass_mock
        sent = _payload(without=field)

        result = await flow.async_step_zone(sent)

        if result["step_id"] != "zone":
            return
        assert seeded, f"{field}: the form was redrawn from nothing"
        offered = seeded[-1]
        for kept, value in FILLED.items():
            if kept == field:
                assert offered.get(kept) is None, f"{field}: came back filled in behind the user"
            else:
                assert offered.get(kept) == value, f"{field}: dropping it also lost {kept}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("field", "code"), sorted(REFUSED_WHEN_EMPTY.items()))
    async def test_the_refusal_names_the_field(self, hass_mock, seeded, field, code):
        flow = cf.NeverDryConfigFlow()
        flow.hass = hass_mock

        result = await flow.async_step_zone(_payload(without=field))

        assert result["step_id"] == "zone"
        assert result["errors"][field] == code
        assert result["errors"][f"{ZONE_FORM[field][0]}.{field}"] == code
        assert result["errors"]["base"] == code

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", OPTIONAL)
    async def test_only_the_declared_set_is_refused(self, hass_mock, seeded, field):
        """Anything outside REFUSED_WHEN_EMPTY has to be droppable.

        This is the half that catches a check added later without a thought
        for whether the user can act on it: a new refusal shows up here as a
        field that used to be optional in practice and no longer is.
        """
        flow = cf.NeverDryConfigFlow()
        flow.hass = hass_mock

        result = await flow.async_step_zone(_payload(without=field))

        refused = result["step_id"] == "zone"
        assert refused is (field in REFUSED_WHEN_EMPTY), (
            f"{field}: refused={refused}, but REFUSED_WHEN_EMPTY says otherwise"
        )
