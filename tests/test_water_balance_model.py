"""Tests for the water-balance model — the *how much*, and the Deficit it returns.

The load-bearing rule of the reference model is that two deficits are comparable
only within one frame. Most of the ``Deficit`` tests are about that, because a
bare float cannot express it and a silent cross-frame comparison is the class of
bug the value object exists to prevent.
"""

from dataclasses import FrozenInstanceError

import pytest
from never_dry.water_balance_model import (
    DEFAULT_ALPHA,
    DEFAULT_T_BASE,
    Deficit,
    ETModel,
    ETStep,
    HargreavesModel,
    HargreavesStep,
    PenmanMonteithModel,
    PenmanStep,
    ReferenceFrame,
    VWCPerZoneModel,
    VWCReading,
    VWCSystemModel,
    vwc_to_fraction,
)


class TestVWCToFraction:
    """The boundary rule that keeps a percentage from silencing a zone (GH #170)."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (0.22, 0.22),  # already a fraction — untouched
            (0.0, 0.0),  # bone dry
            (45.0, 0.45),  # the Ecowitt case: a percentage
            (15.0, 0.15),  # dry, and still a percentage
            (100.0, 1.0),  # saturated, expressed as a percentage
        ],
    )
    def test_reads_both_scales(self, raw, expected):
        assert vwc_to_fraction(raw) == pytest.approx(expected)

    def test_exactly_one_is_saturation_not_one_percent(self):
        """1.0 is a fraction: soil cannot sit at 1 % water content, but it can saturate."""
        assert vwc_to_fraction(1.0) == 1.0

    @pytest.mark.parametrize("raw", [310.0, 500.0, 101.0, -5.0, -0.1, float("nan"), float("inf")])
    def test_rejects_what_is_not_a_water_content(self, raw):
        """Raw ADC counts and negatives are refused, never clamped into a lie."""
        assert vwc_to_fraction(raw) is None


class TestDeficit:
    def test_zero_starts_a_zone_at_nothing(self):
        deficit = Deficit.zero(ReferenceFrame.ET, source="lawn")
        assert deficit.value_mm == 0.0
        assert deficit.source == "lawn"

    def test_clamped_bounds_both_ends(self):
        assert Deficit(-5.0, ReferenceFrame.ET, d_max=100.0).clamped().value_mm == 0.0
        assert Deficit(150.0, ReferenceFrame.ET, d_max=100.0).clamped().value_mm == 100.0

    def test_with_value_does_not_clamp_on_its_own(self):
        """The pair is deliberate: set, then clamp. Callers that want the box ask for it."""
        assert Deficit.zero(ReferenceFrame.ET).with_value(150.0).value_mm == 150.0

    def test_with_value_keeps_frame_and_source(self):
        original = Deficit.zero(ReferenceFrame.VWC_PER_ZONE, source="probe-1")
        moved = original.with_value(7.0)
        assert moved.frame is ReferenceFrame.VWC_PER_ZONE
        assert moved.source == "probe-1"

    def test_projects_onto_an_area(self):
        assert Deficit(10.0, ReferenceFrame.ET).as_liters(50.0) == 500.0

    def test_is_immutable(self):
        with pytest.raises(FrozenInstanceError):
            Deficit(1.0, ReferenceFrame.ET).value_mm = 2.0


class TestReferenceFrames:
    """Comparability is the whole reason the frame travels with the number."""

    def test_shared_frames_compare_by_frame_alone(self):
        a = Deficit(10.0, ReferenceFrame.ET, source="lawn")
        b = Deficit(20.0, ReferenceFrame.ET, source="roses")
        assert a.is_comparable_to(b)

    def test_different_frames_never_compare(self):
        et = Deficit(10.0, ReferenceFrame.ET)
        vwc = Deficit(10.0, ReferenceFrame.VWC_SYSTEM)
        assert not et.is_comparable_to(vwc)

    def test_two_zones_on_their_own_probes_are_not_comparable(self):
        """Each measures a different patch of soil, so the numbers are not the same quantity."""
        a = Deficit(10.0, ReferenceFrame.VWC_PER_ZONE, source="lawn")
        b = Deficit(10.0, ReferenceFrame.VWC_PER_ZONE, source="roses")
        assert not a.is_comparable_to(b)

    def test_the_same_probe_compares_with_itself(self):
        a = Deficit(10.0, ReferenceFrame.VWC_PER_ZONE, source="lawn")
        b = Deficit(14.0, ReferenceFrame.VWC_PER_ZONE, source="lawn")
        assert a.is_comparable_to(b)

    def test_a_per_zone_deficit_without_a_source_compares_with_nothing(self):
        a = Deficit(10.0, ReferenceFrame.VWC_PER_ZONE)
        b = Deficit(10.0, ReferenceFrame.VWC_PER_ZONE)
        assert not a.is_comparable_to(b)

    def test_shared_flag_matches_the_frames(self):
        assert ReferenceFrame.ET.is_shared
        assert ReferenceFrame.VWC_SYSTEM.is_shared
        assert not ReferenceFrame.VWC_PER_ZONE.is_shared


class TestETModel:
    def test_hourly_rate_matches_the_formula(self):
        assert ETModel.et_hourly(20.0) == pytest.approx(DEFAULT_ALPHA * (20.0 - DEFAULT_T_BASE) / 24.0)

    def test_cold_weather_produces_no_evaporation(self):
        """Below the base temperature the rate floors at zero rather than going negative."""
        assert ETModel.et_hourly(DEFAULT_T_BASE - 5.0) == 0.0

    def test_integrates_over_time_and_kc(self):
        model = ETModel(alpha=0.24, t_base=10.0, kc=1.0)
        model.step(ETStep(dt_h=24.0, temp_c=20.0))
        assert model.deficit.value_mm == pytest.approx(0.24 * (20.0 - 10.0) / 24.0 * 24.0)

    def test_kc_scales_the_demand(self):
        thirsty = ETModel(kc=1.0)
        frugal = ETModel(kc=0.5)
        thirsty.step(ETStep(dt_h=24.0, temp_c=25.0))
        frugal.step(ETStep(dt_h=24.0, temp_c=25.0))
        assert frugal.deficit.value_mm == pytest.approx(thirsty.deficit.value_mm / 2)

    def test_rain_is_subtracted(self):
        model = ETModel()
        model.step(ETStep(dt_h=24.0, temp_c=25.0))
        dry = model.deficit.value_mm
        model.step(ETStep(dt_h=0.0, temp_c=25.0, rain_mm=2.0))
        assert model.deficit.value_mm == pytest.approx(max(0.0, dry - 2.0))

    def test_clamps_at_d_max(self):
        model = ETModel(d_max=5.0)
        model.step(ETStep(dt_h=1000.0, temp_c=35.0))
        assert model.deficit.value_mm == 5.0

    def test_reports_the_et_frame(self):
        assert ETModel().reference_frame is ReferenceFrame.ET

    def test_irrigation_reduces_the_deficit(self):
        model = ETModel()
        model.step(ETStep(dt_h=24.0, temp_c=30.0))
        before = model.deficit.value_mm
        model.apply_irrigation(1.0)
        assert model.deficit.value_mm == pytest.approx(before - 1.0)

    def test_reset_clears_it(self):
        model = ETModel()
        model.step(ETStep(dt_h=24.0, temp_c=30.0))
        assert model.reset().value_mm == 0.0

    def test_rejects_the_wrong_input_shape(self):
        with pytest.raises(TypeError):
            ETModel().step(VWCReading(vwc=0.2))


class TestVWCSystemModel:
    def test_deficit_is_read_from_the_probe(self):
        model = VWCSystemModel(field_capacity=0.30, root_depth=0.30)
        model.step(VWCReading(vwc=0.20))
        assert model.deficit.value_mm == pytest.approx((0.30 - 0.20) * 0.30 * 1000)

    def test_above_field_capacity_is_no_deficit(self):
        model = VWCSystemModel(field_capacity=0.30, root_depth=0.30)
        model.step(VWCReading(vwc=0.35))
        assert model.deficit.value_mm == 0.0

    def test_is_stateless_so_readings_do_not_accumulate(self):
        """Two identical readings must not add up — the probe is the truth, not a tally."""
        model = VWCSystemModel(field_capacity=0.30, root_depth=0.30)
        model.step(VWCReading(vwc=0.20))
        first = model.deficit.value_mm
        model.step(VWCReading(vwc=0.20))
        assert model.deficit.value_mm == first

    def test_irrigation_is_a_no_op(self):
        """A stateless probe reports the wetter soil on its own next reading."""
        model = VWCSystemModel()
        model.step(VWCReading(vwc=0.10))
        before = model.deficit.value_mm
        model.apply_irrigation(10.0)
        assert model.deficit.value_mm == before

    def test_reset_is_a_no_op(self):
        model = VWCSystemModel()
        model.step(VWCReading(vwc=0.10))
        assert model.reset().value_mm == model.deficit.value_mm

    def test_reports_a_shared_frame(self):
        assert VWCSystemModel().reference_frame is ReferenceFrame.VWC_SYSTEM

    def test_rejects_the_wrong_input_shape(self):
        with pytest.raises(TypeError):
            VWCSystemModel().step(ETStep(dt_h=1.0, temp_c=20.0))


class TestVWCPerZoneModel:
    def test_reports_a_per_zone_frame_carrying_its_source(self):
        model = VWCPerZoneModel(source="lawn")
        model.step(VWCReading(vwc=0.20))
        assert model.reference_frame is ReferenceFrame.VWC_PER_ZONE
        assert model.deficit.source == "lawn"

    def test_computes_the_same_way_as_the_system_probe(self):
        per_zone = VWCPerZoneModel(source="lawn", field_capacity=0.30, root_depth=0.30)
        system = VWCSystemModel(field_capacity=0.30, root_depth=0.30)
        per_zone.step(VWCReading(vwc=0.18))
        system.step(VWCReading(vwc=0.18))
        assert per_zone.deficit.value_mm == pytest.approx(system.deficit.value_mm)


class TestHigherTiers:
    """Sanity only: the point is that a tier is one rate behind a shared integrator."""

    def test_hargreaves_needs_no_extra_sensor_and_gives_a_plausible_rate(self):
        model = HargreavesModel(latitude_deg=45.0)
        model.step(HargreavesStep(dt_h=24.0, tmax_c=30.0, tmin_c=15.0, day_of_year=196))
        assert 0.0 < model.deficit.value_mm < 15.0

    def test_hargreaves_grows_with_the_temperature_range(self):
        narrow = HargreavesModel(latitude_deg=45.0)
        wide = HargreavesModel(latitude_deg=45.0)
        narrow.step(HargreavesStep(dt_h=24.0, tmax_c=24.0, tmin_c=20.0, day_of_year=196))
        wide.step(HargreavesStep(dt_h=24.0, tmax_c=34.0, tmin_c=10.0, day_of_year=196))
        assert wide.deficit.value_mm > narrow.deficit.value_mm

    def test_penman_monteith_gives_a_plausible_summer_rate(self):
        model = PenmanMonteithModel()
        model.step(
            PenmanStep(dt_h=24.0, temp_c=25.0, rh_pct=50.0, wind_m_s=2.0, net_radiation_mj=15.0),
        )
        assert 0.0 < model.deficit.value_mm < 15.0

    def test_every_et_tier_shares_the_same_frame(self):
        """The seam is the output: different inputs, one comparable quantity."""
        assert ETModel().reference_frame is ReferenceFrame.ET
        assert HargreavesModel(latitude_deg=45.0).reference_frame is ReferenceFrame.ET
        assert PenmanMonteithModel().reference_frame is ReferenceFrame.ET


class TestCapabilityMatch:
    """Which models a site may pick, and what happens when its sensors change.

    The rule is one line — ``declared >= required`` — so what these tests really
    hold is the two halves being written in the same vocabulary. A model added
    to the catalogue without declaring what it needs would be offered to
    everyone, which is the failure the match exists to prevent.
    """

    def test_every_catalogued_model_declares_what_it_needs(self):
        from never_dry.water_balance_model import MODEL_CATALOGUE

        for model in MODEL_CATALOGUE:
            assert isinstance(model.required_sensors, frozenset), model.__name__
            assert model.method_id, model.__name__

    def test_identifiers_are_unique(self):
        """The id is stored in the config entry: a collision would silently swap models."""
        from never_dry.water_balance_model import MODEL_CATALOGUE

        ids = [m.method_id for m in MODEL_CATALOGUE]
        assert len(ids) == len(set(ids))

    def test_a_thermometer_alone_offers_only_the_simple_tier(self):
        from never_dry.environment import Environment
        from never_dry.water_balance_model import ETModel, models_offered_by

        env = Environment(temperature_sensor="sensor.t", rain_sensor="sensor.r")
        assert models_offered_by(env) == (ETModel,)

    def test_declaring_the_richer_sensors_unlocks_penman_without_touching_code(self):
        from never_dry.environment import Environment
        from never_dry.water_balance_model import PenmanMonteithModel, models_offered_by

        env = Environment(
            temperature_sensor="sensor.t",
            rain_sensor="sensor.r",
            humidity_sensor="sensor.h",
            wind_speed_sensor="sensor.w",
            net_radiation_sensor="sensor.rad",
        )
        assert PenmanMonteithModel in models_offered_by(env)

    def test_a_probe_wins_over_the_weather_tiers(self):
        """A measured soil is better evidence than an estimate, so it leads the order."""
        from never_dry.environment import Environment
        from never_dry.water_balance_model import VWCSystemModel, models_offered_by

        env = Environment(
            temperature_sensor="sensor.t",
            rain_sensor="sensor.r",
            soil_moisture_sensor="sensor.vwc",
        )
        assert models_offered_by(env)[0] is VWCSystemModel


class TestBuildModel:
    """Turning a site plus a stored preference into the object that runs."""

    def _bare_site(self):
        from never_dry.environment import Environment

        return Environment(temperature_sensor="sensor.t", rain_sensor="sensor.r")

    def test_without_a_preference_it_reproduces_todays_behaviour(self):
        from never_dry.water_balance_model import ETModel, build_model

        assert isinstance(build_model(self._bare_site()), ETModel)

    def test_a_site_with_a_probe_gets_the_probe_model(self):
        from never_dry.environment import Environment
        from never_dry.water_balance_model import VWCSystemModel, build_model

        env = Environment(temperature_sensor="sensor.t", rain_sensor="sensor.r", soil_moisture_sensor="sensor.vwc")
        assert isinstance(build_model(env), VWCSystemModel)

    def test_a_choice_the_site_cannot_support_degrades_instead_of_failing(self):
        """A sensor can be removed after the choice was stored. Watering must not stop."""
        from never_dry.water_balance_model import ETModel, build_model

        model = build_model(self._bare_site(), method_id="penman_monteith")
        assert isinstance(model, ETModel)

    def test_an_unknown_identifier_falls_back_rather_than_raising(self):
        """A config entry from a future version must not break setup."""
        from never_dry.water_balance_model import ETModel, build_model

        assert isinstance(build_model(self._bare_site(), method_id="no_such_model"), ETModel)

    def test_a_supported_choice_is_honoured_over_the_default_order(self):
        """The user's preference beats the ranking — that is the point of asking."""
        from never_dry.environment import Environment
        from never_dry.water_balance_model import ETModel, build_model

        env = Environment(temperature_sensor="sensor.t", rain_sensor="sensor.r", soil_moisture_sensor="sensor.vwc")
        assert isinstance(build_model(env, method_id="et_simple"), ETModel)

    def test_the_configured_values_reach_the_model(self):
        from never_dry.water_balance_model import build_model

        model = build_model(self._bare_site(), alpha=0.5, t_base=5.0, d_max=42.0)
        assert model.d_max == 42.0
        assert model.step(ETStep(dt_h=24.0, temp_c=15.0)).value_mm == pytest.approx(0.5 * (15.0 - 5.0))


class TestRestore:
    """Adopting a value computed elsewhere — a restart, or a recorder replay."""

    def test_it_adopts_the_value(self):
        model = ETModel()
        assert model.restore(12.5).value_mm == 12.5

    def test_a_stored_value_above_the_current_ceiling_is_clamped(self):
        """d_max can shrink between releases; a stored value must not outlive it."""
        model = ETModel(d_max=10.0)
        assert model.restore(50.0).value_mm == 10.0

    def test_a_negative_stored_value_is_refused(self):
        model = ETModel()
        assert model.restore(-3.0).value_mm == 0.0
