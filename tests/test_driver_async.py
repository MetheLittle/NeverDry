"""Tests for the async half of the driver — commands, delivery, and the master.

The other driver test file covers the pure surface. This one covers the part that
needs a Home Assistant loop: issuing a command and awaiting its confirmation,
delivering water, and the master valve's off-linger.

It matters more than the line count suggests. ``driver.py`` supersedes
``valve_operator.py``, which sits at 99% — but it is not a copy: it shares under
half its lines with the operator and nearly doubles them. The Driver/ZoneDriver/
MasterDriver hierarchy, the delivery strategies and the entity adapter are new
code that no existing test reaches, and they are where valve closure is decided.

Harness follows ``test_valve_operator.py``: a mocked hass, tiny FSM timeouts, and
state confirmations pushed in by hand through ``_on_switch_state``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from never_dry.driver import (
    DeliveryMode,
    DeliveryQuality,
    MasterDriver,
    OperationStatus,
    ZoneDriver,
)
from never_dry.valve_fsm import FsmConfig, ValveState


@pytest.fixture
def hass():
    """Mock HomeAssistant instance suitable for driver tests."""
    hass = MagicMock()
    hass.states = MagicMock()
    hass.states.get = MagicMock(return_value=MagicMock(state="off"))
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()

    def _create_task(coro):
        try:
            return asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()
            return MagicMock()

    hass.async_create_task = _create_task
    return hass


def _fast_fsm() -> FsmConfig:
    """FSM config with tiny timeouts, so a test does not wait on real seconds."""
    return FsmConfig(
        has_flow_meter=False,
        open_timeout_s=0.05,
        close_timeout_s=0.05,
        flow_verify_timeout_s=0.05,
        leak_timeout_s=0.05,
        max_consecutive_failures=3,
    )


def _zone_driver(hass, *, entity_id="switch.valve", flow_rate=60.0, **kwargs) -> ZoneDriver:
    return ZoneDriver(
        hass,
        entity_id,
        delivery_mode=DeliveryMode.ESTIMATED_FLOW,
        flow_rate_lpm=flow_rate,
        fsm_config=_fast_fsm(),
        max_retries=0,
        backoff_s=(0.01,),
        name="testzone",
        **kwargs,
    )


def _state_event(value: str) -> MagicMock:
    event = MagicMock()
    event.data = {"new_state": MagicMock(state=value)}
    return event


async def _yield_loop(times: int = 3) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


async def _confirm(driver, value: str) -> None:
    """Push a state confirmation in, the way HA would."""
    await _yield_loop()
    driver._on_switch_state(_state_event(value))
    await _yield_loop()


def _wire_flowing_valve(hass, *, rate_lpm: float) -> None:
    """Make the mock read as an OPEN valve with water running through the meter.

    Both halves matter: the delivery loop stops the moment the valve reads off,
    so a harness that reports "off" for everything silently exercises the
    fallback path instead of the metered one.
    """

    def states_get(entity_id):
        if entity_id == "sensor.flow":
            state = MagicMock()
            state.state = str(rate_lpm)
            state.attributes = {"unit_of_measurement": "L/min"}
            return state
        return MagicMock(state="on")

    hass.states.get = MagicMock(side_effect=states_get)


class TestCommands:
    """Opening and closing, and the entity adapter picking the right service."""

    async def test_open_confirms_on_a_switch(self, hass):
        driver = _zone_driver(hass)

        async def simulate():
            await _confirm(driver, "on")

        sim = asyncio.create_task(simulate())
        result = await driver.async_turn_on()
        await sim

        assert result.status is OperationStatus.OK
        assert driver.is_open
        hass.services.async_call.assert_any_call("switch", "turn_on", {"entity_id": "switch.valve"}, blocking=False)

    async def test_open_uses_the_valve_service_for_a_valve_entity(self, hass):
        """The GH #94 payoff: a `valve.*` timer is driven without any config migration."""
        driver = _zone_driver(hass, entity_id="valve.bhyve")

        async def simulate():
            await _confirm(driver, "open")

        sim = asyncio.create_task(simulate())
        result = await driver.async_turn_on()
        await sim

        assert result.status is OperationStatus.OK
        hass.services.async_call.assert_any_call("valve", "open_valve", {"entity_id": "valve.bhyve"}, blocking=False)

    async def test_close_confirms(self, hass):
        driver = _zone_driver(hass)

        async def open_then_close():
            await _confirm(driver, "on")

        sim = asyncio.create_task(open_then_close())
        await driver.async_turn_on()
        await sim

        async def simulate_close():
            await _confirm(driver, "off")

        sim2 = asyncio.create_task(simulate_close())
        result = await driver.async_turn_off()
        await sim2

        assert result.status is OperationStatus.OK
        assert not driver.is_open

    async def test_an_unconfirmed_open_fails_rather_than_assuming_success(self, hass):
        """No confirmation must never read as an open valve — the whole safety premise."""
        driver = _zone_driver(hass)
        result = await driver.async_turn_on()
        assert result.status is OperationStatus.FAILED
        assert not driver.is_open

    async def test_repeated_failures_lock_the_valve_in_maintenance(self, hass):
        """After the configured failures the zone is blocked and waits for a human."""
        driver = _zone_driver(hass)
        for _ in range(3):
            await driver.async_turn_on()
        assert driver.is_in_maintenance
        assert driver.state is ValveState.MAINTENANCE

    async def test_maintenance_can_be_cleared(self, hass):
        driver = _zone_driver(hass)
        for _ in range(3):
            await driver.async_turn_on()
        await driver.async_reset_maintenance()
        assert not driver.is_in_maintenance

    async def test_a_command_is_refused_while_in_maintenance(self, hass):
        driver = _zone_driver(hass)
        for _ in range(3):
            await driver.async_turn_on()
        hass.services.async_call.reset_mock()
        result = await driver.async_turn_on()
        assert result.status is OperationStatus.MAINTENANCE
        hass.services.async_call.assert_not_called()

    async def test_unload_is_safe_to_call(self, hass):
        driver = _zone_driver(hass)
        driver.async_unload()
        assert not driver.is_open


class TestDelivery:
    """`deliver()` returns how much water arrived and how truthful that figure is."""

    async def test_nothing_requested_delivers_nothing(self, hass):
        driver = _zone_driver(hass)
        result = await driver.deliver(0.0)
        assert result.liters_delivered == 0.0
        assert result.quality is DeliveryQuality.MEASURED
        hass.services.async_call.assert_not_called()

    async def test_without_a_flow_rate_it_refuses_rather_than_guessing(self, hass):
        """An unknown duration must not become an open valve with no stop condition."""
        driver = _zone_driver(hass, flow_rate=0.0)
        result = await driver.deliver(10.0)
        assert result.liters_delivered == 0.0
        assert result.quality is DeliveryQuality.LOW_CONFIDENCE
        assert result.detail == "no_flow_rate"
        hass.services.async_call.assert_not_called()

    async def test_a_full_run_reports_an_estimated_figure(self, hass):
        """Time-based delivery is an estimate, and says so."""
        driver = _zone_driver(hass, flow_rate=6000.0)  # 0.01 L/s -> 1 L in 0.01 s

        async def simulate():
            await _confirm(driver, "on")
            await asyncio.sleep(0.05)
            driver._on_switch_state(_state_event("off"))

        sim = asyncio.create_task(simulate())
        result = await driver.deliver(1.0)
        await sim

        assert result.quality is DeliveryQuality.ESTIMATED
        assert result.liters_delivered == pytest.approx(1.0, rel=0.2)
        assert result.requested_liters == 1.0

    async def test_an_aborted_run_reports_partial_not_estimated(self, hass):
        """Stopping early must be visible in the quality, not hidden in the number."""
        driver = _zone_driver(hass, flow_rate=6.0)  # 1 L would take 10 s

        aborted = {"now": False}

        async def simulate():
            await _confirm(driver, "on")
            await asyncio.sleep(0.05)
            aborted["now"] = True
            await _yield_loop()
            driver._on_switch_state(_state_event("off"))

        sim = asyncio.create_task(simulate())
        result = await driver.deliver(1.0, should_abort=lambda: aborted["now"])
        await sim

        assert result.quality is DeliveryQuality.PARTIAL
        assert result.liters_delivered < 1.0

    async def test_a_failed_open_delivers_nothing_and_says_why(self, hass):
        driver = _zone_driver(hass, flow_rate=60.0)
        result = await driver.deliver(1.0)
        assert result.liters_delivered == 0.0
        assert result.quality is DeliveryQuality.LOW_CONFIDENCE


class TestMasterDriver:
    """The pump follows aggregate activity — and must not cycle between zones."""

    def _master(self, hass, *, off_delay=0.05) -> MasterDriver:
        return MasterDriver(
            hass,
            "switch.pump",
            off_delay_s=off_delay,
            fsm_config=_fast_fsm(),
            max_retries=0,
            backoff_s=(0.01,),
            name="pump",
        )

    async def test_starts_when_a_zone_becomes_active(self, hass):
        master = self._master(hass)

        async def simulate():
            await _confirm(master, "on")

        sim = asyncio.create_task(simulate())
        await master.follow(any_zone_active=True)
        await sim

        assert master.is_open
        hass.services.async_call.assert_any_call("switch", "turn_on", {"entity_id": "switch.pump"}, blocking=False)

    async def test_stays_on_while_zones_remain_active(self, hass):
        master = self._master(hass)

        async def simulate():
            await _confirm(master, "on")

        sim = asyncio.create_task(simulate())
        await master.follow(any_zone_active=True)
        await sim
        hass.services.async_call.reset_mock()

        await master.follow(any_zone_active=True)
        hass.services.async_call.assert_not_called()

    async def test_closes_after_the_off_delay_once_nothing_is_active(self, hass):
        master = self._master(hass, off_delay=0.02)

        async def simulate_on():
            await _confirm(master, "on")

        sim = asyncio.create_task(simulate_on())
        await master.follow(any_zone_active=True)
        await sim

        await master.follow(any_zone_active=False)
        await asyncio.sleep(0.05)
        master._on_switch_state(_state_event("off"))
        await _yield_loop()

        hass.services.async_call.assert_any_call("switch", "turn_off", {"entity_id": "switch.pump"}, blocking=False)

    async def test_a_new_zone_within_the_delay_cancels_the_close(self, hass):
        """The whole point of the linger: sequential zones must not cycle the pump.

        The field report on GH #95 put it at five seconds — long enough to bridge
        the gap between one zone finishing and the next starting.
        """
        master = self._master(hass, off_delay=0.2)

        async def simulate_on():
            await _confirm(master, "on")

        sim = asyncio.create_task(simulate_on())
        await master.follow(any_zone_active=True)
        await sim
        hass.services.async_call.reset_mock()

        await master.follow(any_zone_active=False)
        await asyncio.sleep(0.02)
        await master.follow(any_zone_active=True)
        await asyncio.sleep(0.3)

        for call in hass.services.async_call.call_args_list:
            assert call.args[1] != "turn_off", "the pump was cycled between two zones"

    async def test_unload_cancels_a_pending_close(self, hass):
        master = self._master(hass, off_delay=0.2)
        await master.follow(any_zone_active=False)
        master.async_unload()
        await asyncio.sleep(0.3)
        for call in hass.services.async_call.call_args_list:
            assert call.args[1] != "turn_off"

    async def test_the_master_knows_nothing_about_liters(self, hass):
        """It is ON/OFF only — no delivery contract, by design."""
        assert not hasattr(self._master(hass), "deliver")


class TestFlowMeterDelivery:
    """Measured delivery: the figure comes from the meter, not from the clock."""

    def _metered(self, hass, **kwargs) -> ZoneDriver:
        return ZoneDriver(
            hass,
            "switch.valve",
            delivery_mode=DeliveryMode.FLOW_METER,
            flow_rate_lpm=60.0,
            flow_meter_sensor=kwargs.pop("meter", "sensor.flow"),
            fsm_config=_fast_fsm(),
            max_retries=0,
            backoff_s=(0.01,),
            name="testzone",
            delivery_timeout_s=1,
            **kwargs,
        )

    async def test_without_a_meter_it_refuses(self, hass):
        driver = self._metered(hass, meter=None)
        result = await driver.deliver(5.0)
        assert result.quality is DeliveryQuality.LOW_CONFIDENCE
        assert result.detail == "no_flow_meter"

    async def test_a_rate_meter_integrates_to_a_measured_figure(self, hass):
        """The payoff over an estimate: what the meter saw, not what the clock implied."""
        driver = self._metered(hass)

        _wire_flowing_valve(hass, rate_lpm=600.0)

        async def simulate():
            await _confirm(driver, "on")
            await asyncio.sleep(0.3)
            driver._on_switch_state(_state_event("off"))

        sim = asyncio.create_task(simulate())
        result = await driver.deliver(1.0)
        await sim

        assert result.liters_delivered > 0
        assert result.quality is DeliveryQuality.MEASURED
        # Guards the harness itself: `fallback_estimate` means the loop never saw
        # an open valve and credited the nominal rate instead of the meter, which
        # is how the first version of this test passed while proving nothing.
        assert result.detail != "fallback_estimate"

    async def test_progress_is_reported_while_water_flows(self, hass):
        """The caller deducts the deficit live, so it needs the running figure."""
        driver = self._metered(hass)
        seen: list[float] = []

        _wire_flowing_valve(hass, rate_lpm=600.0)

        async def simulate():
            await _confirm(driver, "on")
            await asyncio.sleep(0.3)
            driver._on_switch_state(_state_event("off"))

        sim = asyncio.create_task(simulate())
        await driver.deliver(1.0, on_progress=seen.append)
        await sim

        assert seen, "no progress was reported during delivery"
        assert seen == sorted(seen), "progress must not go backwards"

    async def test_a_failed_open_never_reports_water(self, hass):
        driver = self._metered(hass)
        result = await driver.deliver(5.0)
        assert result.liters_delivered == 0.0
        assert result.quality is DeliveryQuality.LOW_CONFIDENCE


class TestLiveness:
    """The active probe — a valve can be unreachable without anyone asking it."""

    async def test_a_responding_entity_is_live(self, hass):
        driver = _zone_driver(hass)
        hass.states.get = MagicMock(return_value=MagicMock(state="off"))
        assert await driver.async_ping() is True

    async def test_an_unavailable_entity_is_not(self, hass):
        driver = _zone_driver(hass)
        hass.states.get = MagicMock(return_value=MagicMock(state="unavailable"))
        assert await driver.async_ping() is False

    async def test_a_missing_entity_is_not(self, hass):
        driver = _zone_driver(hass)
        hass.states.get = MagicMock(return_value=None)
        assert await driver.async_ping() is False

    async def test_a_dedicated_availability_entity_is_preferred(self, hass):
        """A Z2M availability sensor is the cheaper, truer signal when present."""
        driver = _zone_driver(hass, availability_entity="binary_sensor.valve_available")
        asked: list[str] = []

        def states_get(entity_id):
            asked.append(entity_id)
            return MagicMock(state="on")

        hass.states.get = MagicMock(side_effect=states_get)
        assert await driver.async_ping() is True
        assert "binary_sensor.valve_available" in asked

    async def test_an_availability_entity_reading_off_means_unreachable(self, hass):
        driver = _zone_driver(hass, availability_entity="binary_sensor.valve_available")
        hass.states.get = MagicMock(return_value=MagicMock(state="off"))
        assert await driver.async_ping() is False

    async def test_the_probe_can_be_started_and_stopped(self, hass):
        driver = _zone_driver(hass)
        driver.start_liveness_probe(interval_min=0.001)
        await asyncio.sleep(0)
        driver.async_unload()
