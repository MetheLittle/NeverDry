"""The hub's half of GH #313: a stable code and raw params, not finished text.

`_select_model` used to build `_method_reason` as an English sentence, the
defect #313 (second half) reported. It now also sets `_reason_code` and
`_reason_params` — data the card turns into text in the user's language — and
`reason` stays an English sentence alongside them for one release, so a card
older than this change still shows something readable instead of a bare code.

These tests exercise the four branches `_select_model` can take, the same
scenarios `test_et_method_choice.py` already uses for the English text, and
check the code and params side rather than the sentence. `reason_params`
carries the *config slug* the dropdown uses (e.g. "penman_monteith"), not a
translated name — translating it is the card's job now, checked in
`tests/test_card_reason_i18n.py`.
"""

from datetime import datetime

from never_dry.const import (
    CONF_ET_METHOD,
    CONF_HUMIDITY_SENSOR,
    CONF_RAIN_SENSOR,
    CONF_TEMP_SENSOR,
    CONF_WIND_SPEED_SENSOR,
)
from never_dry.sensor import DrynessIndexSensor, WaterBalanceMethodSensor

BARE_SITE = {CONF_TEMP_SENSOR: "sensor.t", CONF_RAIN_SENSOR: "sensor.r"}


class TestReasonCode:
    def test_chosen_explicitly_and_it_ran(self, hass_mock):
        hub = DrynessIndexSensor(hass_mock, {**BARE_SITE, CONF_ET_METHOD: "et_simple"})
        assert WaterBalanceMethodSensor(hub).extra_state_attributes["reason_code"] == "explicit"
        assert WaterBalanceMethodSensor(hub).extra_state_attributes["reason_params"] == {}

    def test_explicit_but_degraded_names_the_requested_slug_not_a_translation(self, hass_mock):
        # penman_monteith on a bare site (temperature + rain only) degrades to
        # hargreaves — the same site test_et_method_choice.py uses for this.
        hub = DrynessIndexSensor(hass_mock, {**BARE_SITE, CONF_ET_METHOD: "penman_monteith"})
        attrs = WaterBalanceMethodSensor(hub).extra_state_attributes
        assert attrs["reason_code"] == "fallback"
        assert attrs["reason_params"] == {"method": "penman_monteith"}
        # The slug, verbatim — not "Penman-Monteith". A translated name here
        # would be exactly the Python-side resolution #313 was asked to drop.
        assert "Penman" not in attrs["reason_params"]["method"]

    def test_automatic_on_a_flat_sensor_carries_the_rounded_range(self, hass_mock):
        hub = DrynessIndexSensor(hass_mock, BARE_SITE)
        hub._select_model(observed_range_c=1.04)
        attrs = WaterBalanceMethodSensor(hub).extra_state_attributes
        assert attrs["reason_code"] == "flat_range"
        assert attrs["reason_params"] == {"range": 1.0}

    def test_automatic_with_no_flat_reading(self, hass_mock):
        hub = DrynessIndexSensor(hass_mock, BARE_SITE)
        hub._select_model(observed_range_c=None)
        attrs = WaterBalanceMethodSensor(hub).extra_state_attributes
        assert attrs["reason_code"] == "auto"
        assert attrs["reason_params"] == {}

    def test_the_english_sentence_still_ships_alongside_the_code(self, hass_mock):
        # One release of backward compatibility, as asked: an older card reads
        # `reason`, a newer one reads `reason_code` — both must be populated.
        hub = DrynessIndexSensor(hass_mock, {**BARE_SITE, CONF_ET_METHOD: "et_simple"})
        assert WaterBalanceMethodSensor(hub).extra_state_attributes["reason"] == "chosen explicitly"


class TestWarmingUpCode:
    def test_the_daily_range_variant(self, hass_mock):
        hub = DrynessIndexSensor(hass_mock, BARE_SITE)
        inputs = hub._warming_up_inputs(temp_c=20.0)
        assert inputs["warming_up_code"] == "daily_range"
        assert inputs["warming_up_params"] == {"hours": 20}
        assert inputs["warming_up_because"]  # English fallback still present

    def test_the_sensor_unreadable_variant(self, hass_mock):
        # Drives the real branch in _build_penman_reading: the day's window is
        # full, so only the unreadable humidity can send it to the fallback. A
        # test that built the override itself would pass with the code removed.
        site = {
            **BARE_SITE,
            CONF_ET_METHOD: "penman_monteith",
            CONF_HUMIDITY_SENSOR: "sensor.h",
            CONF_WIND_SPEED_SENSOR: "sensor.w",
        }
        hub = DrynessIndexSensor(hass_mock, site)
        for hour in range(24):
            hub._diurnal.observe(hour, 12.0 + hour % 12)
        hass_mock.states.get = lambda entity_id: None
        hub._build_penman_reading(dt_h=1.0, temp_c=20.0, rain_mm=0.0, now=datetime(2026, 7, 1, 12, 0))
        assert hub._last_inputs["warming_up_code"] == "sensor_unreadable"
        assert hub._last_inputs["warming_up_params"] == {}
        assert hub._last_inputs["warming_up_because"]  # English fallback still present
