"""The form's answer when a method is picked that the sensors cannot support.

There are two ways to get this wrong, and only one of them is visible. The
loud way is refusing a method the site *can* run. The quiet way is accepting
one it cannot: setup succeeds, the model degrades to the simple tier at build
time, and the user is left believing they are running Penman-Monteith. Nothing
in the interface would say otherwise, and the number would look just as
confident.

So the check that matters is that the form and the runtime answer the same
question with the same rule — which is why the validator builds a real
``Environment`` and asks it, rather than reimplementing the match in
form-shaped code.
"""

from typing import ClassVar

from never_dry.config_flow import _et_method_error
from never_dry.const import (
    CONF_ET_METHOD,
    CONF_HUMIDITY_SENSOR,
    CONF_NET_RADIATION_SENSOR,
    CONF_RAIN_SENSOR,
    CONF_TEMP_MAX_SENSOR,
    CONF_TEMP_MIN_SENSOR,
    CONF_TEMP_SENSOR,
    CONF_VWC_SENSOR,
    CONF_WIND_SPEED_SENSOR,
    ET_METHOD_AUTO,
)

BARE_SITE = {CONF_TEMP_SENSOR: "sensor.t", CONF_RAIN_SENSOR: "sensor.r"}


class TestAutomatic:
    """``auto`` is the promise to pick what the sensors allow, so it never fails."""

    def test_it_is_accepted_on_a_bare_site(self):
        assert _et_method_error({**BARE_SITE, CONF_ET_METHOD: ET_METHOD_AUTO}) is None

    def test_it_is_the_answer_when_the_field_is_absent_entirely(self):
        """An entry saved before this field existed must keep working untouched."""
        assert _et_method_error(BARE_SITE) is None


class TestRefusal:
    """A method whose inputs are missing is refused at the form, not at runtime."""

    def test_penman_without_its_sensors_is_refused(self):
        assert _et_method_error({**BARE_SITE, CONF_ET_METHOD: "penman_monteith"}) == "et_method_missing_sensors"

    def test_hargreaves_needs_both_ends_of_the_day(self):
        half = {**BARE_SITE, CONF_ET_METHOD: "hargreaves", CONF_TEMP_MAX_SENSOR: "sensor.tmax"}
        assert _et_method_error(half) == "et_method_missing_sensors"

    def test_the_probe_model_without_a_probe_is_refused(self):
        assert _et_method_error({**BARE_SITE, CONF_ET_METHOD: "vwc_system"}) == "et_method_missing_sensors"

    def test_an_unknown_method_is_named_as_such(self):
        """A distinct error: nothing the user can add would ever satisfy it."""
        assert _et_method_error({**BARE_SITE, CONF_ET_METHOD: "no_such_method"}) == "et_method_unknown"


class TestAcceptance:
    """Declaring the sensors is the whole unlock — no other setting is involved."""

    def test_the_simple_tier_needs_only_a_thermometer(self):
        assert _et_method_error({**BARE_SITE, CONF_ET_METHOD: "et_simple"}) is None

    def test_penman_is_accepted_once_all_four_are_declared(self):
        rich = {
            **BARE_SITE,
            CONF_ET_METHOD: "penman_monteith",
            CONF_HUMIDITY_SENSOR: "sensor.h",
            CONF_WIND_SPEED_SENSOR: "sensor.w",
            CONF_NET_RADIATION_SENSOR: "sensor.rad",
        }
        assert _et_method_error(rich) is None

    def test_hargreaves_is_accepted_with_both_daily_extremes(self):
        both = {
            **BARE_SITE,
            CONF_ET_METHOD: "hargreaves",
            CONF_TEMP_MAX_SENSOR: "sensor.tmax",
            CONF_TEMP_MIN_SENSOR: "sensor.tmin",
        }
        assert _et_method_error(both) is None

    def test_the_probe_model_is_accepted_with_a_probe(self):
        assert _et_method_error({**BARE_SITE, CONF_ET_METHOD: "vwc_system", CONF_VWC_SENSOR: "sensor.vwc"}) is None

    def test_a_cleared_sensor_field_reads_as_absent_not_as_empty(self):
        """The options form sends nothing for a cleared picker; ``None`` must not satisfy."""
        cleared = {**BARE_SITE, CONF_ET_METHOD: "vwc_system", CONF_VWC_SENSOR: None}
        assert _et_method_error(cleared) == "et_method_missing_sensors"


class TestFormAndRuntimeAgree:
    """The guard for the drift this design can suffer and nothing else would show.

    Two places answer "can this site run this method": the form, so the user is
    told, and ``build_model``, so the right object runs. They are written once
    and called twice today — but nothing structural stops someone adding a
    special case to one of them, and the symptom would be silent. A method the
    form accepts and the builder declines produces a model the user did not
    choose, with no error anywhere.
    """

    SITES: ClassVar[dict] = {
        "bare": BARE_SITE,
        "with_probe": {**BARE_SITE, CONF_VWC_SENSOR: "sensor.vwc"},
        "with_extremes": {
            **BARE_SITE,
            CONF_TEMP_MAX_SENSOR: "sensor.tmax",
            CONF_TEMP_MIN_SENSOR: "sensor.tmin",
        },
        "full_weather": {
            **BARE_SITE,
            CONF_HUMIDITY_SENSOR: "sensor.h",
            CONF_WIND_SPEED_SENSOR: "sensor.w",
            CONF_NET_RADIATION_SENSOR: "sensor.rad",
        },
    }

    def _environment_for(self, site):
        from never_dry.environment import Environment

        return Environment(
            temperature_sensor=site.get(CONF_TEMP_SENSOR) or "",
            rain_sensor=site.get(CONF_RAIN_SENSOR) or "",
            soil_moisture_sensor=site.get(CONF_VWC_SENSOR),
            humidity_sensor=site.get(CONF_HUMIDITY_SENSOR),
            wind_speed_sensor=site.get(CONF_WIND_SPEED_SENSOR),
            net_radiation_sensor=site.get(CONF_NET_RADIATION_SENSOR),
            temp_max_sensor=site.get(CONF_TEMP_MAX_SENSOR),
            temp_min_sensor=site.get(CONF_TEMP_MIN_SENSOR),
        )

    def test_an_accepted_method_is_the_one_that_actually_runs(self):
        from never_dry.water_balance_model import MODEL_CATALOGUE, build_model

        checked = 0
        for site in self.SITES.values():
            for model in MODEL_CATALOGUE:
                accepted = _et_method_error({**site, CONF_ET_METHOD: model.method_id}) is None
                built = build_model(self._environment_for(site), method_id=model.method_id)
                if accepted:
                    assert isinstance(built, model), (
                        f"the form accepted {model.method_id} but the builder ran {type(built).__name__}"
                    )
                else:
                    assert not isinstance(built, model), (
                        f"the form refused {model.method_id} but the builder ran it anyway"
                    )
                checked += 1
        assert checked == len(self.SITES) * len(MODEL_CATALOGUE)

    def test_automatic_always_produces_something_runnable(self):
        """Whatever the site declares, ``auto`` must land on a model, never on nothing."""
        from never_dry.water_balance_model import WaterBalanceModel, build_model

        for site in self.SITES.values():
            assert isinstance(build_model(self._environment_for(site)), WaterBalanceModel)
