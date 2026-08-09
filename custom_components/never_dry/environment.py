"""The site — what the installation *has*, and what it can therefore compute.

This module materializes the object the RFC in ``docs/design_domain_object_model.md``
calls :class:`Environment`: the user's declared answer to "which sensors do you
have?", plus the shared quantities that belong to the sky rather than to any one
zone.

It replaces the object previously called **System**, which was a catch-all
bundling three unrelated responsibilities. The RFC dissolves it rather than
renaming it, redistributing what it held:

===========================  ==================================================
System attribute (before)    Now lives in
===========================  ==================================================
temperature + rain sensors   :class:`Environment` — environmental feeds
alpha (ET sensitivity)       ``ETModel`` — used *only* by the simple ET tier
D_max (deficit clamp)        ``Zone`` — the value is the zone's soil reservoir;
                             only the clamping *mechanism* is shared
master valve / pump          ``MasterActuator`` — a hydraulics concern
===========================  ==================================================

**Capability matching** is the reason this object earns its place. Each
water-balance model declares the sensors it requires; a zone may offer only the
models whose requirements this site satisfies::

    Environment.declared_sensors  >=  model.required_sensors   =>  model offered

So a user with a thermometer gets the simple ET tier; add humidity, wind and net
radiation and Penman-Monteith unlocks; add a soil probe and VWC becomes
available — with no model selectable by hand that the hardware cannot feed.

Design intent — this module is deliberately **pure**: no Home Assistant import,
no I/O. It holds *bindings* (entity ids as opaque strings) and the rules about
them, never the readings; resolving a binding to a value is the integration's
job. Same choice as ``water_balance_model.py``, for the same reason: the rules
are trivially testable when nothing has to be mocked.

**Phase 1 — inert scaffold.** Nothing imports this module yet. Wiring
``DrynessIndexSensor`` onto it is a deliberate later phase, and is gated on the
`Zone` class (``zone.py``) landing too: the two are the halves of the same seam.

References: ``docs/design_domain_object_model.md`` (RFC: dissolve ``System``),
``docs/design_water_balance_reference_model.md`` (D3, yearly rain as a shared
quantity), GH #146 (site exposure, the per-zone counterpart to this object).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

# Defaults mirror ``const.py`` so an Environment built with no overrides behaves
# exactly like today's system sensor. Kept as module constants (not a HA import)
# to keep the module pure; the integration passes the user-configured values in.
DEFAULT_LATITUDE: float = 45.0
DEFAULT_BACKFILL_DAYS: int = 90
DEFAULT_RAIN_DELAY_THRESHOLD: float = 0.60
DEFAULT_RAIN_DELAY_HOURS: float = 12.0


class SensorKind(StrEnum):
    """A kind of environmental input a water-balance model may require.

    Deliberately about the *quantity*, not the entity: two thermometers are one
    ``TEMPERATURE`` capability. This is the vocabulary both sides of the
    capability match are written in.
    """

    TEMPERATURE = "temperature"
    RAIN = "rain"
    HUMIDITY = "humidity"
    WIND_SPEED = "wind_speed"
    NET_RADIATION = "net_radiation"
    TEMP_MAX = "temp_max"
    TEMP_MIN = "temp_min"
    SOIL_MOISTURE = "soil_moisture"
    RAIN_PROBABILITY = "rain_probability"


#: Which :class:`Environment` attribute carries the binding for each sensor kind.
#: Module-level rather than a dataclass field: it describes the class, not an
#: installation, and is identical for every instance.
BINDING_BY_KIND: dict[SensorKind, str] = {
    SensorKind.TEMPERATURE: "temperature_sensor",
    SensorKind.RAIN: "rain_sensor",
    SensorKind.HUMIDITY: "humidity_sensor",
    SensorKind.WIND_SPEED: "wind_speed_sensor",
    SensorKind.NET_RADIATION: "net_radiation_sensor",
    SensorKind.TEMP_MAX: "temp_max_sensor",
    SensorKind.TEMP_MIN: "temp_min_sensor",
    SensorKind.SOIL_MOISTURE: "soil_moisture_sensor",
    SensorKind.RAIN_PROBABILITY: "rain_probability_sensor",
}


class RainSensorType(StrEnum):
    """How the rain binding reports, which decides how a delta is derived.

    Mirrors ``const.py``. Carried here because it is a property of the *feed*,
    not of any zone: it says how to read the sensor, not what to do with it.
    """

    CUMULATIVE = "cumulative"
    ROLLING = "rolling"
    EVENT = "event"


@dataclass(frozen=True)
class RainDelayPolicy:
    """Forecast-driven delay: *the signal*, not the decision.

    The environment supplies "rain is likely"; it never skips a watering itself.
    Whether a given zone honours the delay is the zone's business — an indoor or
    patio zone is unaffected, which is why the gate lives on ``Zone.placement``
    (see the RFC). Keeping the threshold here and the gate there is what stops
    forecast rain and measured rain from ever disagreeing about a zone.
    """

    enabled: bool = False
    probability_threshold: float = DEFAULT_RAIN_DELAY_THRESHOLD
    delay_hours: float = DEFAULT_RAIN_DELAY_HOURS

    def triggers_at(self, probability: float | None) -> bool:
        """``True`` when a forecast probability is high enough to delay watering."""
        if not self.enabled or probability is None:
            return False
        return probability >= self.probability_threshold


@dataclass
class Environment:
    """The declared sensor inventory of one installation, plus the shared sky.

    Holds *bindings* — opaque entity-id strings — never readings. A binding that
    is ``None`` means "the user does not have this sensor", which is exactly the
    input the capability match needs.
    """

    # ── Feeds: what the user declared at install ────────────────────────────
    temperature_sensor: str | None = None
    rain_sensor: str | None = None
    humidity_sensor: str | None = None
    wind_speed_sensor: str | None = None
    net_radiation_sensor: str | None = None
    temp_max_sensor: str | None = None
    temp_min_sensor: str | None = None
    soil_moisture_sensor: str | None = None
    rain_probability_sensor: str | None = None

    # ── How to read them ────────────────────────────────────────────────────
    rain_sensor_type: RainSensorType = RainSensorType.EVENT
    backfill_days: int = DEFAULT_BACKFILL_DAYS

    # ── Site constants ──────────────────────────────────────────────────────
    # Latitude is a property of the place, not of a sensor: Hargreaves needs it
    # for the astronomical radiation term, and the seasonal Kc curve needs it to
    # flip for the southern hemisphere.
    latitude: float = DEFAULT_LATITUDE

    # ── Policy the site offers, that zones consume ──────────────────────────
    rain_delay: RainDelayPolicy = field(default_factory=RainDelayPolicy)

    # ── Shared quantity: one sky over the whole garden ──────────────────────
    # Rain that fell this calendar year [mm]. A site quantity by nature, so every
    # zone mirrors the same figure instead of keeping its own drifting counter
    # (reference model D3). Note the asymmetry with the deficit, which is
    # emphatically *not* shared: rain falls on the garden, deficit belongs to a
    # patch of soil.
    yearly_rain_mm: float = 0.0
    yearly_rain_year: int | None = None

    # ── Capability matching ─────────────────────────────────────────────────

    @property
    def declared_sensors(self) -> frozenset[SensorKind]:
        """Every :class:`SensorKind` the user actually bound to an entity."""
        return frozenset(kind for kind, attr in BINDING_BY_KIND.items() if getattr(self, attr) is not None)

    def binding_for(self, kind: SensorKind) -> str | None:
        """The entity id bound to ``kind``, or ``None`` when undeclared."""
        return getattr(self, BINDING_BY_KIND[kind], None)

    def satisfies(self, required: frozenset[SensorKind] | set[SensorKind]) -> bool:
        """``True`` when this site declares everything ``required`` asks for.

        The whole capability rule, in one line: ``declared >= required``.
        """
        return self.declared_sensors >= frozenset(required)

    def missing_for(self, required: frozenset[SensorKind] | set[SensorKind]) -> frozenset[SensorKind]:
        """What ``required`` asks for and this site does not have.

        The complement of :meth:`satisfies`, kept separate because the UI needs
        to say *which* sensor unlocks a model, not merely that one is missing.
        """
        return frozenset(required) - self.declared_sensors

    # ── Shared-rain bookkeeping ─────────────────────────────────────────────

    def accrue_yearly_rain(self, rain_mm: float, *, year: int) -> Environment:
        """Return a copy with ``rain_mm`` added to the yearly total.

        Rolls over when ``year`` differs from the stored one. Credits only
        positive increments: a decreasing reading is never rain (GH #123), and
        that rule belongs with the feed rather than with each consumer.
        """
        if rain_mm <= 0 and self.yearly_rain_year == year:
            return self
        if self.yearly_rain_year != year:
            return replace(self, yearly_rain_mm=max(0.0, rain_mm), yearly_rain_year=year)
        return replace(self, yearly_rain_mm=self.yearly_rain_mm + rain_mm)

    def reset_yearly_rain(self, *, year: int) -> Environment:
        """Return a copy with the yearly rain total cleared (user-invoked reset)."""
        return replace(self, yearly_rain_mm=0.0, yearly_rain_year=year)

    def yearly_rain_liters(self, area_m2: float) -> float:
        """Project the yearly rain onto an area: 1 mm over 1 m² is 1 litre."""
        return self.yearly_rain_mm * area_m2
