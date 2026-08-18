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
        driver.schedule_flow_sample = lambda meter, baseline, session_s: captured.update(
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
        driver.schedule_flow_sample = lambda meter, baseline, session_s: captured.update(baseline=baseline)

        await driver._deliver_by_volume(40.0, "sensor.meter", None, None)

        assert captured["baseline"] == 1000.0


class TestShortSessionsAreRefused:
    """On a counter stepping in whole liters, a short run is mostly quantization."""

    def test_a_session_below_the_minimum_is_never_scheduled(self):
        hass = _hass_with_meter([1000.0])
        driver = _driver(hass)
        driver._hass = MagicMock()

        driver.schedule_flow_sample("sensor.meter", 1000.0, MIN_SESSION_S - 1)

        driver._hass.async_create_task.assert_not_called()

    def test_a_long_enough_session_is_scheduled(self):
        hass = _hass_with_meter([1000.0])
        driver = _driver(hass)
        driver._hass = MagicMock()

        driver.schedule_flow_sample("sensor.meter", 1000.0, MIN_SESSION_S + 1)

        driver._hass.async_create_task.assert_called_once()


class TestTheFlowVerificationWindowComesFromTheZone:
    """GH #173: a fixed 10 s window cannot pass on a slow zone with a coarse meter.

    rpatel3001: 1 L of resolution at 1.2 L/min needs ~50 s before the counter
    can move at all, so the check failed however healthy the valve was. The
    window has to be resolution over flow rate, not a constant.
    """

    def _driver_with(self, resolution=None, lpm=6.0):
        from never_dry.driver import FLOW_VERIFY_MARGIN  # noqa: F401

        hass = _hass_with_meter([0.0])
        driver = _driver(hass)
        driver._flow_rate_lpm = lpm
        if resolution:
            driver._session_flow.resolution_l = resolution
        return driver

    def test_without_a_known_resolution_it_is_conservative_not_strict(self):
        """The old constant is what closed working valves; absence must not be strict."""
        from never_dry.driver import FLOW_VERIFY_UNKNOWN_RESOLUTION_S

        window, verdict = self._driver_with().flow_verify_window()
        assert window == FLOW_VERIFY_UNKNOWN_RESOLUTION_S
        assert window > 10.0
        assert verdict is None

    def test_the_reported_case_gets_a_window_that_can_pass(self):
        """1 L at 1.2 L/min → ~50 s to first tick, so the window must exceed it."""
        driver = self._driver_with(resolution=1.0, lpm=1.2)
        assert driver.time_to_first_tick_s() == pytest.approx(50.0)
        window, verdict = driver.flow_verify_window()
        assert window > 50.0
        assert verdict is None

    def test_a_fast_zone_keeps_a_tight_window(self):
        """A meter that ticks immediately must not buy a lax guard."""
        from never_dry.driver import FLOW_VERIFY_MIN_S

        window, verdict = self._driver_with(resolution=0.1, lpm=20.0).flow_verify_window()
        assert window == FLOW_VERIFY_MIN_S
        assert verdict is None

    def test_a_hopeless_meter_is_declared_unverifiable_not_failed(self):
        """28 L of resolution: no window both passes healthy and catches dead.

        Stretching it would make "commanded but dry" take minutes to notice,
        and that detection is a safety layer. So the verification steps aside
        instead of growing without limit.
        """
        from never_dry.driver import FLOW_VERIFY_MAX_S

        driver = self._driver_with(resolution=28.0, lpm=1.0)
        window, verdict = driver.flow_verify_window()
        assert window == FLOW_VERIFY_MAX_S
        assert verdict is not None
        assert "not applicable" in verdict

    def test_the_learned_rate_is_what_sizes_the_window(self):
        """Design rate 360 L/h against 205 measured would size the window short."""
        driver = self._driver_with(resolution=1.0, lpm=6.0)
        for _ in range(3):
            driver._session_flow.window.record(3.4)  # 205 L/h measured
        assert driver.effective_flow_lpm == pytest.approx(3.4)
        # Slower real flow ⇒ longer wait before the counter can move.
        assert driver.time_to_first_tick_s() == pytest.approx(60.0 / 3.4, abs=0.1)


class TestTheMeterTeachesItsOwnResolution:
    def test_the_smallest_increment_wins(self):
        from never_dry.session_flow import SessionFlowTracker

        hass = _hass_with_meter([0.0])
        tracker = SessionFlowTracker(hass, "switch.valve")
        for step in (3.0, 1.0, 2.0):
            tracker.observe_step(step)
        assert tracker.resolution_l == 1.0

    def test_a_non_increment_teaches_nothing(self):
        from never_dry.session_flow import SessionFlowTracker

        hass = _hass_with_meter([0.0])
        tracker = SessionFlowTracker(hass, "switch.valve")
        assert tracker.observe_step(0.0) is False
        assert tracker.resolution_l is None


class TestTheDriverIsWiredWithWhatItNeeds:
    """Field-found: the driver was built without the zone's flow rate.

    Everything derived from `resolution / flow rate` silently fell back to a
    blanket constant, because the driver's rate was 0 and the derivation
    returned nothing. The unit tests all passed — they constructed the driver
    themselves and passed the rate. Only a live session showed the gap, so the
    wiring itself is asserted here.
    """

    def test_the_zone_setup_passes_the_design_flow_rate(self):
        import inspect

        from never_dry import sensor as sensor_module

        src = inspect.getsource(sensor_module._setup_controller)
        assert "flow_rate_lpm=" in src, (
            "ZoneDriver is built without flow_rate_lpm: the flow-verification "
            "window cannot be derived and falls back to a constant"
        )

    def test_without_a_rate_the_derivation_declines_instead_of_guessing(self):
        hass = _hass_with_meter([0.0])
        driver = _driver(hass)
        driver._flow_rate_lpm = 0.0
        driver._session_flow.resolution_l = 1.0
        assert driver.time_to_first_tick_s() is None


class TestThePressIsVisibleBeforeItsOutcome:
    """A valve command announces itself, so a slow open is not a dead button.

    Field, 2026-08-18: a sleeping Zigbee valve spent 48 s in retries before
    failing. For all of that time the card was unchanged, which is exactly
    what a press that went nowhere looks like.
    """

    @pytest.mark.asyncio
    async def test_the_zone_is_flagged_while_the_valve_is_being_opened(self):
        from never_dry.controller import IrrigationController

        zone = MagicMock()
        zone.valve = "switch.valve"
        seen = []
        zone.set_awaiting_valve = lambda v: seen.append(v)

        ctrl = IrrigationController.__new__(IrrigationController)
        ctrl._zones = {"z": zone}
        ctrl._active_valve = None
        ctrl._hass = MagicMock()
        ctrl._open_valve_inner = AsyncMock(return_value=True)

        await IrrigationController._open_valve(ctrl, "switch.valve")

        assert seen == [True, False], "the flag must be raised before the attempt and cleared after"

    @pytest.mark.asyncio
    async def test_the_flag_is_cleared_even_when_the_open_fails(self):
        """A failed open is precisely when a stuck flag would mislead most."""
        from never_dry.controller import IrrigationController

        zone = MagicMock()
        zone.valve = "switch.valve"
        seen = []
        zone.set_awaiting_valve = lambda v: seen.append(v)

        ctrl = IrrigationController.__new__(IrrigationController)
        ctrl._zones = {"z": zone}
        ctrl._active_valve = None
        ctrl._hass = MagicMock()
        ctrl._open_valve_inner = AsyncMock(side_effect=RuntimeError("radio down"))

        with pytest.raises(RuntimeError):
            await IrrigationController._open_valve(ctrl, "switch.valve")

        assert seen == [True, False]


class TestTheSeamsAreThePublicOnes:
    """Cross-object calls must go through public names, or a rename breaks silently.

    Found by inspection, not by a failing test: the controller reached into
    `driver._schedule_flow_sample`, and when that was made public the driver's
    own call site kept the old name. Nothing failed, because the driver's
    delivery loop is not the one production takes — so the test suite could not
    see it. These assert the seam itself.
    """

    def test_the_driver_exposes_the_flow_sample_seam_publicly(self):
        from never_dry.driver import ZoneDriver

        assert hasattr(ZoneDriver, "schedule_flow_sample")
        assert not hasattr(ZoneDriver, "_schedule_flow_sample")

    def test_the_driver_calls_its_own_seam_by_the_public_name(self):
        import inspect

        from never_dry.driver import ZoneDriver

        src = inspect.getsource(ZoneDriver._deliver_by_volume)
        assert "self.schedule_flow_sample(" in src
        assert "self._schedule_flow_sample(" not in src

    def test_the_controller_does_not_reach_into_driver_privates(self):
        import inspect

        from never_dry import controller as controller_module

        src = inspect.getsource(controller_module)
        assert "driver._" not in src, "the controller must use the driver's public seams"
