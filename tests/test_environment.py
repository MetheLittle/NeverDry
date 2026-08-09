"""Tests for the site — the declared sensor inventory and what it unlocks.

Capability matching is the reason this object exists, so most of these are about
one rule: a zone may offer only the models the site can feed.
"""

from never_dry.environment import (
    BINDING_BY_KIND,
    Environment,
    RainDelayPolicy,
    RainSensorType,
    SensorKind,
)

# What each water-balance tier asks of the site.
NEEDS_ET = {SensorKind.TEMPERATURE}
NEEDS_HARGREAVES = {SensorKind.TEMP_MAX, SensorKind.TEMP_MIN}
NEEDS_PENMAN = {
    SensorKind.TEMPERATURE,
    SensorKind.HUMIDITY,
    SensorKind.WIND_SPEED,
    SensorKind.NET_RADIATION,
}
NEEDS_VWC = {SensorKind.SOIL_MOISTURE}


class TestDeclaredSensors:
    def test_a_bare_site_declares_nothing(self):
        assert Environment().declared_sensors == frozenset()

    def test_only_bound_sensors_are_declared(self):
        env = Environment(temperature_sensor="sensor.t", rain_sensor="sensor.r")
        assert env.declared_sensors == {SensorKind.TEMPERATURE, SensorKind.RAIN}

    def test_every_sensor_kind_has_a_binding(self):
        """A kind with no attribute behind it would silently never be declarable."""
        assert set(BINDING_BY_KIND) == set(SensorKind)

    def test_every_binding_names_a_real_attribute(self):
        env = Environment()
        for attr in BINDING_BY_KIND.values():
            assert hasattr(env, attr), attr

    def test_binding_for_returns_the_entity_id(self):
        env = Environment(soil_moisture_sensor="sensor.probe")
        assert env.binding_for(SensorKind.SOIL_MOISTURE) == "sensor.probe"
        assert env.binding_for(SensorKind.HUMIDITY) is None


class TestCapabilityMatching:
    def test_a_thermometer_unlocks_the_baseline_tier_only(self):
        env = Environment(temperature_sensor="sensor.t")
        assert env.satisfies(NEEDS_ET)
        assert not env.satisfies(NEEDS_PENMAN)
        assert not env.satisfies(NEEDS_VWC)

    def test_adding_sensors_unlocks_penman(self):
        env = Environment(
            temperature_sensor="sensor.t",
            humidity_sensor="sensor.rh",
            wind_speed_sensor="sensor.wind",
            net_radiation_sensor="sensor.rn",
        )
        assert env.satisfies(NEEDS_PENMAN)

    def test_hargreaves_needs_no_extra_hardware_beyond_min_max(self):
        env = Environment(temp_max_sensor="sensor.tmax", temp_min_sensor="sensor.tmin")
        assert env.satisfies(NEEDS_HARGREAVES)

    def test_missing_for_names_what_is_absent(self):
        """The UI has to say *which* sensor unlocks a model, not merely that one is missing."""
        env = Environment(temperature_sensor="sensor.t")
        assert env.missing_for(NEEDS_PENMAN) == {
            SensorKind.HUMIDITY,
            SensorKind.WIND_SPEED,
            SensorKind.NET_RADIATION,
        }

    def test_missing_for_is_empty_when_satisfied(self):
        env = Environment(temperature_sensor="sensor.t")
        assert env.missing_for(NEEDS_ET) == frozenset()

    def test_extra_sensors_never_block_a_model(self):
        """The rule is `declared >= required`, not equality."""
        env = Environment(temperature_sensor="sensor.t", soil_moisture_sensor="sensor.p")
        assert env.satisfies(NEEDS_ET)

    def test_a_model_requiring_nothing_is_always_satisfied(self):
        assert Environment().satisfies(set())


class TestYearlyRain:
    """One sky over the whole garden — a site quantity, unlike the deficit."""

    def test_accrues_positive_increments(self):
        env = Environment().accrue_yearly_rain(12.4, year=2026).accrue_yearly_rain(3.6, year=2026)
        assert env.yearly_rain_mm == 16.0

    def test_ignores_a_decrease(self):
        """A falling reading is never rain — the lesson of GH #123."""
        env = Environment().accrue_yearly_rain(10.0, year=2026)
        assert env.accrue_yearly_rain(-5.0, year=2026).yearly_rain_mm == 10.0

    def test_ignores_zero(self):
        env = Environment().accrue_yearly_rain(10.0, year=2026)
        assert env.accrue_yearly_rain(0.0, year=2026).yearly_rain_mm == 10.0

    def test_rolls_over_on_a_new_year(self):
        env = Environment().accrue_yearly_rain(80.0, year=2026)
        rolled = env.accrue_yearly_rain(2.0, year=2027)
        assert rolled.yearly_rain_mm == 2.0
        assert rolled.yearly_rain_year == 2027

    def test_rollover_clamps_a_negative_first_increment(self):
        env = Environment().accrue_yearly_rain(80.0, year=2026)
        assert env.accrue_yearly_rain(-3.0, year=2027).yearly_rain_mm == 0.0

    def test_reset_clears_the_total(self):
        env = Environment().accrue_yearly_rain(80.0, year=2026).reset_yearly_rain(year=2026)
        assert env.yearly_rain_mm == 0.0

    def test_projects_onto_an_area(self):
        """1 mm over 1 m² is 1 litre."""
        env = Environment().accrue_yearly_rain(16.0, year=2026)
        assert env.yearly_rain_liters(50.0) == 800.0

    def test_accrual_returns_a_copy(self):
        """The dataclass is mutable, but accrual is expressed as a new value."""
        env = Environment()
        assert env.accrue_yearly_rain(5.0, year=2026) is not env
        assert env.yearly_rain_mm == 0.0


class TestRainDelayPolicy:
    """The site supplies the signal; it never skips a watering itself."""

    def test_disabled_by_default(self):
        assert not RainDelayPolicy().triggers_at(0.99)

    def test_triggers_at_or_above_the_threshold(self):
        policy = RainDelayPolicy(enabled=True, probability_threshold=0.6)
        assert policy.triggers_at(0.6)
        assert policy.triggers_at(0.8)
        assert not policy.triggers_at(0.59)

    def test_no_forecast_never_triggers(self):
        """An unavailable forecast must not read as "rain is coming"."""
        assert not RainDelayPolicy(enabled=True).triggers_at(None)


class TestDefaults:
    def test_rain_sensor_type_defaults_to_event(self):
        assert Environment().rain_sensor_type is RainSensorType.EVENT

    def test_latitude_defaults_to_the_northern_hemisphere(self):
        assert Environment().latitude > 0
