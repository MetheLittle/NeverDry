"""Water-balance model abstraction — the single home for computing *how much*
water a patch of soil needs, independent of *how* it is measured.

This module materializes two domain-model concepts that today live implicitly,
scattered inside ``sensor.py`` (``DrynessIndexSensor`` / ``ETSensor`` and the
per-zone deficit loop):

* :class:`WaterBalanceModel` (abstract) — the *scientific model*: "give me the
  deficit, no matter which inputs I compute it from". Its three concrete
  strategies mirror the reference frames of
  ``docs/design_water_balance_reference_model.md``:
  :class:`ETModel` (temperature + rain), :class:`VWCSystemModel` (one system
  soil-moisture probe), :class:`VWCPerZoneModel` (a per-zone probe, target
  AI-174).
* :class:`Deficit` (value object) — the *quantity*: millimetres **plus the
  reference frame they are defined against**. The load-bearing rule of the
  reference model is that *two deficits are comparable only if they share a
  frame*, so a bare ``float`` is not enough — the frame travels with the value.

Design intent — this module is deliberately **pure**: no Home Assistant import,
no I/O, only arithmetic on floats. The "how much water" math has no reason to
touch HA, which makes it trivially testable and reusable. This mirrors, on the
*sensing* side, what ``actuator.py`` did on the *actuation* side: extract the
implicit domain object into a self-contained module now, wire the existing call
sites onto it in a later phase.

The seam is the **output**, not the input: the three models share no inputs
(ET needs weather, VWC needs a probe), but every model produces a
:class:`Deficit` in mm. This is exactly why the abstraction sits at the output —
each model copes with whatever sensors it has and exposes the same quantity.

Translation chain (see ``docs/design_domain_object_model.md``)::

    WaterBalanceModel  ──produces──▶  Deficit (mm)  ──Zone──▶  Actuator (liters)

References: ``docs/design_water_balance_reference_model.md`` (D1-D5, reference
frames), ``docs/design_domain_object_model.md`` (the domain classes),
GH #123 (the deficit reference-frame bug this model makes impossible).

**Phase 1 — inert scaffold.** Nothing imports this module yet; wiring
``DrynessIndexSensor`` / the per-zone loop onto it is a deliberate later phase.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import ClassVar

# Defaults mirror ``const.py`` so a model built with no overrides behaves exactly
# like today's sensors. Kept as module constants (not a HA import) to keep the
# module pure; the integration passes the user-configured values in.
DEFAULT_ALPHA: float = 0.22
DEFAULT_T_BASE: float = 9.0
DEFAULT_D_MAX: float = 100.0
DEFAULT_FIELD_CAPACITY: float = 0.30
DEFAULT_ROOT_DEPTH: float = 0.30
DEFAULT_KC: float = 1.0

# Metres → millimetres. A VWC fraction over a root depth in metres yields metres
# of water; x1000 expresses the deficit in the model's canonical millimetres.
_M_TO_MM: float = 1000.0


def _clamp(value: float, lower: float, upper: float) -> float:
    """Clamp ``value`` into ``[lower, upper]`` (the FAO-56 ``[0, D_max]`` box)."""
    return max(lower, min(value, upper))


# ── Reference frame: what a deficit is measured *relative to* ───────────────


class ReferenceFrame(StrEnum):
    """The measurement frame a :class:`Deficit` is defined against.

    Comparability follows the reference model: ``ET`` and ``VWC_SYSTEM`` are
    **shared** across zones (all zones read the same feeds/probe, differing only
    by Kc), so their deficits are comparable. ``VWC_PER_ZONE`` is **not** shared:
    each zone measures a different patch of soil, so two per-zone deficits are
    comparable only when they come from the *same* probe.
    """

    ET = "et"
    VWC_SYSTEM = "vwc_system"
    VWC_PER_ZONE = "vwc_per_zone"

    @property
    def is_shared(self) -> bool:
        """``True`` when every zone shares this frame (ET feeds or one system probe)."""
        return self is not ReferenceFrame.VWC_PER_ZONE


# ── The quantity: a deficit in mm, carrying its reference frame ─────────────


@dataclass(frozen=True)
class Deficit:
    """A soil water deficit in millimetres, tagged with its :class:`ReferenceFrame`.

    Immutable value object. It is the common currency every
    :class:`WaterBalanceModel` returns and the Zone consumes to decide watering.
    The ``frame`` (and, for per-zone probes, ``source``) travel with the number
    so the "two deficits are comparable only within one frame" rule of the
    reference model is enforceable in code rather than left to convention.
    """

    value_mm: float
    frame: ReferenceFrame
    d_max: float = DEFAULT_D_MAX
    # Identity of the frame for per-zone deficits (e.g. the probe/zone id). For
    # shared frames (ET, system VWC) it is ``None`` — the frame alone identifies
    # comparability. See :meth:`is_comparable_to`.
    source: str | None = None

    @classmethod
    def zero(cls, frame: ReferenceFrame, d_max: float = DEFAULT_D_MAX, source: str | None = None) -> Deficit:
        """Return a fresh zero deficit — a new zone starts here (reference model D4)."""
        return cls(0.0, frame, d_max, source)

    def clamped(self) -> Deficit:
        """Return a copy with ``value_mm`` clamped into the FAO-56 ``[0, d_max]`` box."""
        return replace(self, value_mm=_clamp(self.value_mm, 0.0, self.d_max))

    def with_value(self, value_mm: float) -> Deficit:
        """Return a copy at ``value_mm`` (unclamped); pair with :meth:`clamped`."""
        return replace(self, value_mm=value_mm)

    def is_comparable_to(self, other: Deficit) -> bool:
        """``True`` when ``self`` and ``other`` live in the same reference frame.

        Shared frames (ET, system VWC) are comparable whenever the frame matches.
        Per-zone frames additionally require the same ``source`` — two zones each
        on their own probe are *not* comparable even though both are
        ``VWC_PER_ZONE`` (reference model, "Reference frames").
        """
        if self.frame is not other.frame:
            return False
        if self.frame.is_shared:
            return True
        return self.source is not None and self.source == other.source

    def as_liters(self, area_m2: float) -> float:
        """Project this deficit onto a zone area: 1 mm over 1 m² is 1 litre."""
        return self.value_mm * area_m2


# ── Model inputs: each strategy consumes its own reading ────────────────────


@dataclass(frozen=True)
class ETStep:
    """One integration step for :class:`ETModel`: a time delta plus its weather.

    ``dt_h`` is the hours elapsed since the previous step (forward-Euler variable
    step, as today's event-driven integrator); ``temp_c`` the current temperature
    in °C; ``rain_mm`` the rain credited over this step in mm.
    """

    dt_h: float
    temp_c: float
    rain_mm: float = 0.0


@dataclass(frozen=True)
class VWCReading:
    """One reading for a VWC model: the current volumetric water content.

    ``vwc`` is a volumetric fraction in ``[0, 1]`` (e.g. ``0.22`` = 22 %),
    directly comparable to ``field_capacity``.
    """

    vwc: float


# Union of everything a model's :meth:`WaterBalanceModel.step` may accept.
ModelInput = ETStep | VWCReading


# ── The abstract water-balance model (the "how much" seam) ──────────────────


class WaterBalanceModel(abc.ABC):
    """A strategy that turns sensor inputs into a per-frame :class:`Deficit` in mm.

    Concrete models differ in *what they read* (weather vs a moisture probe) and
    in whether they are **stateful** (ET integrates over time) or **stateless**
    (VWC recomputes each reading), but they all expose the same contract:
    :meth:`step` advances the model and returns the current deficit,
    :meth:`apply_irrigation` registers delivered water, :meth:`reset` returns to
    zero. The Zone talks to this interface and never to a specific model.
    """

    #: Whether the model accumulates state across steps (ET) or recomputes each
    #: reading from scratch (VWC). Subclasses set it as a class attribute.
    is_stateful: ClassVar[bool]

    def __init__(self, *, d_max: float = DEFAULT_D_MAX, initial_mm: float = 0.0, source: str | None = None) -> None:
        """Initialise the model at ``initial_mm`` (default 0 — reference model D4)."""
        self._d_max = d_max
        self._source = source
        self._value_mm = _clamp(initial_mm, 0.0, d_max)

    @property
    @abc.abstractmethod
    def reference_frame(self) -> ReferenceFrame:
        """The frame every :class:`Deficit` from this model is defined against."""

    @property
    def d_max(self) -> float:
        """The FAO-56 upper clamp on the deficit [mm]."""
        return self._d_max

    @property
    def deficit(self) -> Deficit:
        """The current deficit as a frame-tagged value object."""
        source = self._source if self.reference_frame is ReferenceFrame.VWC_PER_ZONE else None
        return Deficit(round(self._value_mm, 4), self.reference_frame, self._d_max, source)

    @abc.abstractmethod
    def step(self, inputs: ModelInput) -> Deficit:
        """Advance the model with one reading and return the updated deficit."""

    def apply_irrigation(self, delivered_mm: float) -> Deficit:
        """Register ``delivered_mm`` of water just applied and return the new deficit.

        Stateful models subtract it (and clamp at 0); stateless models override
        to a no-op because their next reading already reflects the wetter soil.
        """
        self._value_mm = _clamp(self._value_mm - delivered_mm, 0.0, self._d_max)
        return self.deficit

    def reset(self) -> Deficit:
        """Reset the deficit to zero (a zone was fully irrigated) and return it."""
        self._value_mm = 0.0
        return self.deficit


# ── Strategy 1: ET water balance (temperature + rain, stateful) ─────────────


class ETModel(WaterBalanceModel):
    """Forward-Euler ET water balance: ``D += ET_h · Kc · Δt - rain``, clamped.

    The evapotranspiration demand is the same simplified linear estimate the
    integration uses today (:func:`et_hourly`). ``Kc`` (the crop coefficient) is
    a **Zone** attribute in the domain model, not a property of the physics: a
    per-zone model instance is built with that zone's ``kc``, while the *system
    reference* model uses ``kc = 1.0``. Each zone owns its own instance because
    irrigation resets are per-zone and independent — a shared reference cannot be
    scaled proportionally after the fact (reference model D1/D4).
    """

    is_stateful: ClassVar[bool] = True

    def __init__(
        self,
        *,
        alpha: float = DEFAULT_ALPHA,
        t_base: float = DEFAULT_T_BASE,
        kc: float = DEFAULT_KC,
        d_max: float = DEFAULT_D_MAX,
        initial_mm: float = 0.0,
    ) -> None:
        """Configure ET sensitivity ``alpha``, base temperature ``t_base`` and ``kc``."""
        super().__init__(d_max=d_max, initial_mm=initial_mm)
        self._alpha = alpha
        self._t_base = t_base
        self._kc = kc

    @property
    def reference_frame(self) -> ReferenceFrame:
        """ET deficits are shared across zones (same temperature + rain feeds)."""
        return ReferenceFrame.ET

    @property
    def kc(self) -> float:
        """The crop coefficient this instance integrates with (1.0 for the reference)."""
        return self._kc

    @staticmethod
    def et_hourly(temp_c: float, *, alpha: float = DEFAULT_ALPHA, t_base: float = DEFAULT_T_BASE) -> float:
        """Instantaneous ET estimate [mm/h] — ``max(0, alpha · (T - T_base) / 24)``.

        The single source of the ET formula today written twice (``ETSensor`` and
        ``DrynessIndexSensor``); the abstraction unifies it here.
        """
        return max(0.0, alpha * (temp_c - t_base) / 24.0)

    def step(self, inputs: ModelInput) -> Deficit:
        """Integrate one ``ETStep``: add ``ET_h · Kc · Δt``, subtract rain, clamp."""
        if not isinstance(inputs, ETStep):
            raise TypeError(f"ETModel.step expects ETStep, got {type(inputs).__name__}")
        et_h = self.et_hourly(inputs.temp_c, alpha=self._alpha, t_base=self._t_base)
        self._value_mm = _clamp(
            self._value_mm + et_h * self._kc * inputs.dt_h - inputs.rain_mm,
            0.0,
            self._d_max,
        )
        return self.deficit


# ── Strategy 2: VWC from a system probe (stateless measurement) ─────────────


class VWCSystemModel(WaterBalanceModel):
    """Deficit read directly from one system soil-moisture probe (stateless).

    ``D = (field_capacity - vwc) · root_depth · 1000``, clamped to ``[0, d_max]``.
    Stateless: every reading recomputes the deficit from the current measurement,
    so there is no drift and no seeding — which is why the interim system-level
    VWC deficit is benign (reference model D5). All zones scale the same current
    reading by their Kc downstream; the frame is shared.
    """

    is_stateful: ClassVar[bool] = False

    def __init__(
        self,
        *,
        field_capacity: float = DEFAULT_FIELD_CAPACITY,
        root_depth: float = DEFAULT_ROOT_DEPTH,
        d_max: float = DEFAULT_D_MAX,
    ) -> None:
        """Configure ``field_capacity`` (fraction) and ``root_depth`` (metres)."""
        super().__init__(d_max=d_max)
        self._field_capacity = field_capacity
        self._root_depth = root_depth

    @property
    def reference_frame(self) -> ReferenceFrame:
        """A single system probe is a shared frame across zones."""
        return ReferenceFrame.VWC_SYSTEM

    def step(self, inputs: ModelInput) -> Deficit:
        """Recompute the deficit from a ``VWCReading`` — no accumulated state."""
        if not isinstance(inputs, VWCReading):
            raise TypeError(f"{type(self).__name__}.step expects VWCReading, got {type(inputs).__name__}")
        self._value_mm = _clamp(
            (self._field_capacity - inputs.vwc) * self._root_depth * _M_TO_MM,
            0.0,
            self._d_max,
        )
        return self.deficit

    def apply_irrigation(self, delivered_mm: float) -> Deficit:
        """No-op: a stateless probe reflects the wetter soil on its next reading."""
        return self.deficit

    def reset(self) -> Deficit:
        """No-op: there is no accumulated state to clear (the probe is the truth)."""
        return self.deficit


# ── Strategy 3: VWC from a per-zone probe (target, AI-174) ──────────────────


class VWCPerZoneModel(VWCSystemModel):
    """Deficit from a zone's *own* soil-moisture probe (the AI-174 target).

    Same stateless measurement as :class:`VWCSystemModel`, but the frame is
    **per-zone**: each zone measures a different patch of soil, so its deficit is
    not comparable with a sibling's (the ``source`` identity — the probe/zone id
    — guards this in :meth:`Deficit.is_comparable_to`). When this lands, the
    system-level VWC deficit disappears entirely (reference model D5).
    """

    def __init__(
        self,
        *,
        source: str,
        field_capacity: float = DEFAULT_FIELD_CAPACITY,
        root_depth: float = DEFAULT_ROOT_DEPTH,
        d_max: float = DEFAULT_D_MAX,
    ) -> None:
        """Configure a per-zone VWC model; ``source`` identifies the zone/probe frame."""
        super().__init__(field_capacity=field_capacity, root_depth=root_depth, d_max=d_max)
        self._source = source

    @property
    def reference_frame(self) -> ReferenceFrame:
        """A per-zone probe is *not* shared: deficits differ patch by patch."""
        return ReferenceFrame.VWC_PER_ZONE
