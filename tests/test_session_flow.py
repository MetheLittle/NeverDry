"""Tests for the flow rate learned from real irrigation sessions.

The field data behind these: a zone declared at 360 L/h that 43 measured
sessions put at 205 L/h. What matters is that the learned figure comes from
the meter and the clock, and that a session which cannot support a figure
produces none rather than a plausible one.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from never_dry.driver import OperationStatus, ZoneDriver
from never_dry.session_flow import (
    MIN_SAMPLES,
    MIN_SESSION_S,
    WINDOW_SIZE,
    SessionFlowWindow,
)
from never_dry.valve_fsm import FsmConfig


class TestTheWindowOnlySpeaksWhenItCan:
    """A median of one session is an anecdote; the window must say so."""

    def test_it_reports_nothing_below_the_minimum(self):
        window = SessionFlowWindow()
        for _ in range(MIN_SAMPLES - 1):
            window.record(3.0)
        assert window.median_lpm() is None

    def test_it_reports_once_it_has_enough(self):
        window = SessionFlowWindow()
        for _ in range(MIN_SAMPLES):
            window.record(3.0)
        assert window.median_lpm() == 3.0

    def test_the_median_ignores_a_session_that_went_wrong(self):
        """A run cut short by a timeout lands far away; the mean would follow it."""
        window = SessionFlowWindow()
        for _ in range(4):
            window.record(3.4)
        window.record(0.01)  # meter stalled, valve open for an hour
        assert window.median_lpm() == pytest.approx(3.4)

    def test_it_forgets_beyond_the_window(self):
        window = SessionFlowWindow()
        for _ in range(WINDOW_SIZE + 10):
            window.record(1.0)
        assert window.sample_count == WINDOW_SIZE

    def test_the_diagnostics_show_the_spread_the_median_came_from(self):
        window = SessionFlowWindow()
        for value in (3.0, 3.4, 4.0):
            window.record(value)
        report = window.as_dict()
        assert report["sample_count"] == 3
        assert report["median_lpm"] == pytest.approx(3.4)
        assert report["median_lph"] == pytest.approx(204.0)
        assert report["min_lpm"] == pytest.approx(3.0)
        assert report["max_lpm"] == pytest.approx(4.0)


def _hass_with_meter(readings):
    """A hass mock whose meter returns ``readings`` in order, then the last one."""
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    sequence = list(readings)

    def states_get(entity_id):
        state = MagicMock()
        state.state = str(sequence.pop(0) if len(sequence) > 1 else sequence[0])
        state.attributes = {"unit_of_measurement": "L"}
        return state

    hass.states.get = MagicMock(side_effect=states_get)

    def _create_task(coro):
        try:
            return asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()
            return MagicMock()

    hass.async_create_task = _create_task
    return hass


def _driver(hass) -> ZoneDriver:
    return ZoneDriver(
        hass,
        "switch.valve",
        flow_rate_lpm=6.0,
        fsm_config=FsmConfig(has_flow_meter=True),
        max_retries=0,
        backoff_s=(0.01,),
        name="testzone",
    )


class TestTheSampleComesFromTheMeterAndTheClock:
    @pytest.mark.asyncio
    async def test_it_divides_the_counter_difference_by_the_session(self, monkeypatch):
        """100 L over 10 minutes is 10 L/min, whatever the zone was configured at."""
        monkeypatch.setattr("never_dry.driver.asyncio.sleep", AsyncMock())
        hass = _hass_with_meter([1100.0])
        driver = _driver(hass)

        await driver._record_flow_sample("sensor.meter", baseline=1000.0, session_s=600.0)

        assert driver.measured_flow_lpm is None  # one sample is not a median yet
        assert driver._session_flow.window.sample_count == 1
        assert driver._session_flow.window._samples[0] == pytest.approx(10.0)

    @pytest.mark.asyncio
    async def test_it_waits_before_reading_so_late_ticks_still_count(self, monkeypatch):
        """The last tick of a session routinely lands after the valve is shut."""
        sleeper = AsyncMock()
        monkeypatch.setattr("never_dry.driver.asyncio.sleep", sleeper)
        hass = _hass_with_meter([1050.0])
        driver = _driver(hass)

        await driver._record_flow_sample("sensor.meter", baseline=1000.0, session_s=600.0)

        sleeper.assert_awaited_once()
        assert sleeper.await_args.args[0] > 0

    @pytest.mark.asyncio
    async def test_a_counter_that_did_not_move_yields_no_sample(self, monkeypatch):
        monkeypatch.setattr("never_dry.driver.asyncio.sleep", AsyncMock())
        hass = _hass_with_meter([1000.0])
        driver = _driver(hass)

        await driver._record_flow_sample("sensor.meter", baseline=1000.0, session_s=600.0)

        assert driver._session_flow.window.sample_count == 0

    @pytest.mark.asyncio
    async def test_a_counter_that_reset_yields_no_sample(self, monkeypatch):
        """A reset makes the difference negative; a guess would be worse than a gap."""
        monkeypatch.setattr("never_dry.driver.asyncio.sleep", AsyncMock())
        hass = _hass_with_meter([5.0])
        driver = _driver(hass)

        await driver._record_flow_sample("sensor.meter", baseline=1000.0, session_s=600.0)

        assert driver._session_flow.window.sample_count == 0

    @pytest.mark.asyncio
    async def test_an_unreadable_meter_yields_no_sample(self, monkeypatch):
        monkeypatch.setattr("never_dry.driver.asyncio.sleep", AsyncMock())
        hass = _hass_with_meter(["unavailable"])
        driver = _driver(hass)

        await driver._record_flow_sample("sensor.meter", baseline=1000.0, session_s=600.0)

        assert driver._session_flow.window.sample_count == 0


class TestTheDeliveryLoopHandsOverTheRightBaseline:
    """The baseline must be the meter *before* the valve opened, and nothing else."""

    @pytest.mark.asyncio
    async def test_it_samples_from_the_reading_taken_before_opening(self, monkeypatch):
        monkeypatch.setattr("never_dry.driver.asyncio.sleep", AsyncMock())
        hass = _hass_with_meter([1000.0, 1000.0, 1040.0])
        driver = _driver(hass)
        driver.async_turn_on = AsyncMock(return_value=MagicMock(status=OperationStatus.OK))
        driver.async_turn_off = AsyncMock()
        driver._driver_is_off = MagicMock(return_value=False)
        captured = {}
        driver._schedule_flow_sample = lambda meter, baseline, session_s: captured.update(
            meter=meter, baseline=baseline, session_s=session_s
        )

        await driver._deliver_by_volume(40.0, "sensor.meter", None, None)

        assert captured["baseline"] == 1000.0
        assert captured["meter"] == "sensor.meter"

    @pytest.mark.asyncio
    async def test_a_mid_session_counter_reset_does_not_move_the_baseline(self, monkeypatch):
        """The loop rebases ``initial`` on a reset; the baseline must not follow.

        If it did, the reset would be handed over as a phantom volume and the
        learned rate would jump by whatever the counter had accumulated.
        """
        monkeypatch.setattr("never_dry.driver.asyncio.sleep", AsyncMock())
        # 1000 before opening, then the counter resets and climbs from zero.
        hass = _hass_with_meter([1000.0, 5.0, 45.0])
        driver = _driver(hass)
        driver.async_turn_on = AsyncMock(return_value=MagicMock(status=OperationStatus.OK))
        driver.async_turn_off = AsyncMock()
        driver._driver_is_off = MagicMock(return_value=False)
        captured = {}
        driver._schedule_flow_sample = lambda meter, baseline, session_s: captured.update(baseline=baseline)

        await driver._deliver_by_volume(40.0, "sensor.meter", None, None)

        assert captured["baseline"] == 1000.0


class TestShortSessionsAreRefused:
    """On a counter stepping in whole liters, a short run is mostly quantization."""

    def test_a_session_below_the_minimum_is_never_scheduled(self):
        hass = _hass_with_meter([1000.0])
        driver = _driver(hass)
        driver._hass = MagicMock()

        driver._schedule_flow_sample("sensor.meter", 1000.0, MIN_SESSION_S - 1)

        driver._hass.async_create_task.assert_not_called()

    def test_a_long_enough_session_is_scheduled(self):
        hass = _hass_with_meter([1000.0])
        driver = _driver(hass)
        driver._hass = MagicMock()

        driver._schedule_flow_sample("sensor.meter", 1000.0, MIN_SESSION_S + 1)

        driver._hass.async_create_task.assert_called_once()
