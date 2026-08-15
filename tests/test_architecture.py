"""Architectural invariants, enforced here rather than in review.

The domain model — `Zone`, the water-balance models, `Environment`,
`Scheduler`, and the `Driver` hierarchy — was written before it was wired, so
for a while two implementations of the same rules existed side by side. That
is survivable while it is *known*; it stops being survivable when a fix lands
in the copy that does not run, which is what happened with the already-open
valve confirmation (it went into `driver.py`, and production kept the bug).

These tests hold the three properties that keep the migration honest:

1. the domain modules stay **pure** — no Home Assistant, no I/O;
2. they never depend **upward** on the layer that consumes them;
3. which of them are wired is a **declared fact**, checked against reality,
   so "inert scaffold" cannot quietly stop being true — as it already did
   once, in `zone.py`'s own docstring, for two releases.

Wiring an entity therefore means moving one name in `WIRED` below and
watching what breaks. That is the intended workflow, not an obstacle to it.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "never_dry"

#: Modules that carry domain rules and must remain free of Home Assistant.
#: `driver.py` is deliberately absent: it is an *actuator*, HA-aware by design
#: (see its module docstring), so purity does not apply to it.
PURE_DOMAIN_MODULES = ("zone", "water_balance_model", "environment", "scheduler")

#: The Home-Assistant-coupled layer. The domain must never import it.
HA_LAYER_MODULES = ("sensor", "controller", "valve_operator", "config_flow", "diagnostics", "button", "driver")

#: Domain modules the integration actually imports today. Move a name here in
#: the same commit that wires it — the test below fails in both directions, so
#: neither a silent wiring nor a stale claim can pass.
WIRED = {"zone", "water_balance_model", "environment", "scheduler"}

#: The counterpart: written, tested, and reached by nothing but the tests.
INERT = {"driver"}

#: Phrase the scaffolds use to announce they are not wired. A module that says
#: this while being imported is the exact drift these tests exist to catch.
INERT_CLAIM = "Nothing imports this module yet"


# ── Helpers ───────────────────────────────────────────────────────────


def _imported_names(module: str) -> set[str]:
    """Every module name imported by ``module``, absolute and relative alike."""
    tree = ast.parse((PACKAGE / f"{module}.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import: `from .zone import Zone` has
            # module == "zone". A bare `from . import x` has module None.
            names.add(node.module or "")
            if node.level:
                names.update(alias.name for alias in node.names)
    return names


def _integration_modules() -> list[str]:
    """Every module of the package, domain and HA layer together."""
    return sorted(p.stem for p in PACKAGE.glob("*.py") if p.stem != "__init__")


def _importers_of(target: str) -> set[str]:
    """Which modules of the package import ``target``."""
    return {
        module
        for module in _integration_modules()
        if module != target and any(name == target or name.endswith(f".{target}") for name in _imported_names(module))
    }


# ── Purity ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("module", PURE_DOMAIN_MODULES)
def test_domain_module_is_pure(module):
    """No Home Assistant import: the rules must be testable without a runtime.

    This is what lets the water balance and the scheduling rules be exercised
    directly, and it is the property most easily lost — one convenience import
    of `homeassistant.util.dt` is enough.
    """
    ha_imports = sorted(name for name in _imported_names(module) if name.split(".")[0] == "homeassistant")
    assert not ha_imports, f"{module}.py imports Home Assistant: {ha_imports}"


@pytest.mark.parametrize("module", PURE_DOMAIN_MODULES)
def test_domain_module_does_not_depend_upward(module):
    """The domain does not import the layer that consumes it.

    A cycle here would mean the rules can no longer be read, or moved, without
    the entity layer coming along.
    """
    imported = _imported_names(module)
    upward = sorted(name for name in HA_LAYER_MODULES if name in imported)
    assert not upward, f"{module}.py imports the HA layer: {upward}"


# ── Wiring status as a declared fact ──────────────────────────────────


def test_declared_wiring_covers_every_domain_module():
    """`WIRED` and `INERT` must together describe the whole domain, exactly once."""
    declared = WIRED | INERT
    known = set(PURE_DOMAIN_MODULES) | {"driver"}
    assert not (WIRED & INERT), f"declared both wired and inert: {sorted(WIRED & INERT)}"
    assert declared == known, (
        f"undeclared domain modules: {sorted(known - declared)}; unknown names declared: {sorted(declared - known)}"
    )


@pytest.mark.parametrize("module", sorted(WIRED))
def test_module_declared_wired_is_imported_by_the_integration(module):
    """A module declared wired must be reached from production, not only tests."""
    importers = _importers_of(module)
    assert importers, f"{module}.py is declared WIRED but nothing in the package imports it"


@pytest.mark.parametrize("module", sorted(INERT))
def test_module_declared_inert_is_imported_by_nothing(module):
    """The other direction: wiring something without saying so also fails.

    This is the half that matters. A scaffold that quietly becomes load-bearing
    is how the second source of truth is born.
    """
    importers = _importers_of(module)
    assert not importers, (
        f"{module}.py is declared INERT but is imported by {sorted(importers)} — "
        f"move it to WIRED in the same commit that wires it"
    )


# ── One formula, one home ─────────────────────────────────────────────
#
# Wiring a domain object is not finished when the object is called: it is
# finished when the copy it replaced is gone. Only a test can hold that, and its
# absence is why three copies of the crop coefficient and two of the settle
# bookkeeping survived for months.
#
# The list grows as each wiring completes — it is a ledger of what has actually
# been consolidated, not an aspiration. Still outstanding, deliberately: the
# deficit-to-litres conversion (`volume_liters` on the entity, `water_demand_l`
# on the Zone) and the seasonal Kc curve, both waiting on the Zone completion.

SINGLE_HOME_FORMULAS = (
    (r"alpha\s*\*\s*\(", "water_balance_model", "the ET rate"),
    (r"field_cap\w*\s*-\s*vwc", "water_balance_model", "the VWC deficit"),
    (r"efficiency\s*/\s*self\s*\.\s*area_m2", "zone", "crediting delivered water"),
)


def _executable_source(module: str) -> str:
    """Module source with comments and string literals removed.

    A formula named in a docstring is documentation, not a second copy — and
    every one of these formulas is *described* in prose somewhere on purpose.
    Only code counts, so the tokens that are not code are dropped.
    """
    import io
    import tokenize

    source = (PACKAGE / f"{module}.py").read_bytes()
    pieces: list[str] = []
    for tok in tokenize.tokenize(io.BytesIO(source).readline):
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            continue
        pieces.append(tok.string)
    return " ".join(pieces)


@pytest.mark.parametrize(("pattern", "home", "what"), SINGLE_HOME_FORMULAS)
def test_formula_has_a_single_home(pattern, home, what):
    """The formula may appear in exactly one module's executable code."""
    import re

    found = sorted(m for m in _integration_modules() if re.search(pattern, _executable_source(m)))
    assert found == [home], f"{what} should live only in {home}.py, found in {found}"


# ── "Mirrors const.py" has to be true ─────────────────────────────────


def test_domain_enums_mirror_const():
    """A scaffold that says it mirrors ``const.py`` must actually mirror it.

    ``RainSensorType`` declared three values against the two the integration
    ships. The missing third was not an oversight on the shipped side: telling a
    midnight-reset total from a rolling window was removed deliberately, because
    guessing between them wiped deficits at 05:00 on one and dropped overnight
    rain on the other (GH #123). A scaffold still offering the choice would have
    reintroduced the bug the day it was wired.
    """
    from never_dry.const import (
        IRRIGATION_MODE_MANUAL,
        IRRIGATION_MODE_REACTIVE,
        IRRIGATION_MODE_SCHEDULED,
        RAIN_TYPE_DAILY_TOTAL,
        RAIN_TYPE_EVENT,
    )
    from never_dry.environment import RainSensorType
    from never_dry.zone import IrrigationMode

    assert {t.value for t in RainSensorType} == {RAIN_TYPE_EVENT, RAIN_TYPE_DAILY_TOTAL}
    assert {m.value for m in IrrigationMode} == {
        IRRIGATION_MODE_MANUAL,
        IRRIGATION_MODE_REACTIVE,
        IRRIGATION_MODE_SCHEDULED,
    }


@pytest.mark.parametrize(
    ("domain_module", "domain_name", "const_name"),
    [
        ("zone", "DEFAULT_EFFICIENCY", "DEFAULT_EFFICIENCY"),
        ("zone", "DEFAULT_THRESHOLD_MM", "DEFAULT_THRESHOLD"),
        ("zone", "DEFAULT_MICROCLIMATE_FACTOR", "DEFAULT_MICROCLIMATE_FACTOR"),
        ("water_balance_model", "DEFAULT_ALPHA", "DEFAULT_ALPHA"),
        ("water_balance_model", "DEFAULT_T_BASE", "DEFAULT_T_BASE"),
        ("water_balance_model", "DEFAULT_D_MAX", "DEFAULT_D_MAX"),
        ("water_balance_model", "DEFAULT_FIELD_CAPACITY", "DEFAULT_FIELD_CAPACITY"),
        ("water_balance_model", "DEFAULT_ROOT_DEPTH", "DEFAULT_ROOT_DEPTH"),
        ("water_balance_model", "DEFAULT_KC", "DEFAULT_KC"),
        ("environment", "DEFAULT_BACKFILL_DAYS", "DEFAULT_BACKFILL_DAYS"),
        ("scheduler", "DEFAULT_MIN_SERVICE_INTERVAL_S", "MIN_SERVICE_INTERVAL_S"),
    ],
)
def test_domain_defaults_mirror_const(domain_module, domain_name, const_name):
    """The pure modules keep their own copies so they stay importable alone.

    Copies drift. These are the ones that claim to be copies, checked against
    the originals — a one-line guard against a whole class of silent divergence.
    """
    import importlib

    from never_dry import const

    mod = importlib.import_module(f"never_dry.{domain_module}")
    assert getattr(mod, domain_name) == getattr(const, const_name), (
        f"{domain_module}.{domain_name} has drifted from const.{const_name}"
    )


@pytest.mark.parametrize("module", sorted(WIRED))
def test_a_wired_module_does_not_claim_to_be_inert(module):
    """Its docstring has to agree with its status.

    `zone.py` said "Nothing imports this module yet" for two releases while
    `sensor.py` delegated the whole zone state to it. The claim a reader meets
    first was the one that was wrong.
    """
    source = (PACKAGE / f"{module}.py").read_text(encoding="utf-8")
    docstring = ast.get_docstring(ast.parse(source)) or ""
    assert INERT_CLAIM not in docstring, f"{module}.py is wired but its docstring still says {INERT_CLAIM!r}"
