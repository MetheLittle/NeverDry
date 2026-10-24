"""The operating mode of an Environment: active, away, stopped.

``docs/design/scheduler.md``, section 9.4. Each test pins one sentence of that
section, because the failures here are quiet ones: a stopped garden that still
opens a valve in January, a holiday spent under "water these plants", an amber
chip all winter about a valve that is in the shed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ServiceValidationError
from never_dry.environment import Environment, OperatingMode
from never_dry.scheduler import Scheduler, SkipReason
from never_dry.select import OperatingModeSelect, mode_key
from never_dry.valve_notifier import NotificationKind, Severity, ValveNotifier
from never_dry.zone import Zone


def _call(data):
    call = MagicMock()
    call.data = data
    return call


def _event(entity_id, old, new):
    event = MagicMock()
    event.data = {
        "entity_id": entity_id,
        "old_state": MagicMock(state=old),
        "new_state": MagicMock(state=new),
    }
    return event


# ── The rule itself ─────────────────────────────────────────────────


class TestOperatingModeRule:
    @pytest.mark.parametrize(
        ("mode", "commands", "answers", "notifies", "asks"),
        [
            (OperatingMode.ACTIVE, True, True, True, True),
            (OperatingMode.AWAY, True, True, True, False),
            (OperatingMode.STOPPED, False, False, False, False),
        ],
    )
    def test_what_each_mode_allows(self, mode, commands, answers, notifies, asks):
        assert mode.sends_commands is commands
        assert mode.expects_answers is answers
        assert mode.notifies is notifies
        assert mode.asks_a_person is asks

    def test_environment_defaults_to_active(self):
        assert Environment().operating_mode is OperatingMode.ACTIVE

    def test_declaring_a_mode_leaves_the_rest_of_the_site_alone(self):
        env = Environment(temperature_sensor="sensor.t", yearly_rain_mm=12.0)
        stopped = env.with_operating_mode("stopped")
        assert stopped.operating_mode is OperatingMode.STOPPED
        assert stopped.temperature_sensor == "sensor.t"
        assert stopped.yearly_rain_mm == 12.0
        assert env.operating_mode is OperatingMode.ACTIVE


# ── The scheduler: stopped is a skip, asked first ───────────────────


class TestSchedulerStopped:
    @pytest.fixture
    def dry_zone(self):
        zone = Zone(name="Orto", area_m2=10.0, threshold_mm=5.0)
        zone.deficit = zone.deficit.with_value(40.0)
        return zone

    def test_scheduled_run_is_skipped_however_dry(self, dry_zone):
        decision = Scheduler().evaluate_scheduled(dry_zone, is_running=False, mode=OperatingMode.STOPPED)
        assert not decision.should_irrigate
        assert decision.reason is SkipReason.STOPPED

    def test_reactive_run_is_skipped_however_dry(self, dry_zone):
        decision = Scheduler().evaluate_reactive(dry_zone, is_running=False, mode=OperatingMode.STOPPED)
        assert decision.reason is SkipReason.STOPPED

    def test_away_leaves_the_valves_working(self, dry_zone):
        assert Scheduler().evaluate_scheduled(dry_zone, is_running=False, mode=OperatingMode.AWAY).should_irrigate
        assert Scheduler().evaluate_reactive(dry_zone, is_running=False, mode=OperatingMode.AWAY).should_irrigate


# ── The notifier: one gate for every notification ───────────────────


class TestNotifierGate:
    @pytest.fixture
    def notifier(self):
        hass = MagicMock()
        hass.services.async_call = AsyncMock()
        return ValveNotifier(hass)

    @pytest.mark.asyncio
    async def test_stopped_withholds_even_the_leak_alarm(self, notifier):
        await notifier.apply_operating_mode(OperatingMode.STOPPED)
        assert await notifier.notify("Orto", NotificationKind.STUCK_OPEN, Severity.CRITICAL) is False
        assert await notifier.notify("Orto", NotificationKind.LEAK_DETECTED, Severity.CRITICAL) is False
        assert await notifier.notify(None, NotificationKind.WATER_ME_NOW, context={"zones": "x"}) is False
        notifier._hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_away_withholds_only_the_requests_to_a_person(self, notifier):
        await notifier.apply_operating_mode(OperatingMode.AWAY)
        assert await notifier.notify(None, NotificationKind.WATER_ME_NOW, context={"zones": "x"}) is False
        assert notifier.admits(NotificationKind.STUCK_OPEN)
        assert notifier.admits(NotificationKind.BATTERY_LOW)

    @pytest.mark.asyncio
    async def test_switching_withdraws_what_the_new_mode_does_not_admit(self, notifier):
        await notifier.notify(None, NotificationKind.WATER_ME_NOW, context={"zones": "x"})
        await notifier.notify("Orto", NotificationKind.BATTERY_LOW, context={"level": 5})
        assert await notifier.apply_operating_mode(OperatingMode.AWAY) == 1
        assert not notifier.is_active(None, NotificationKind.WATER_ME_NOW)
        assert notifier.is_active("Orto", NotificationKind.BATTERY_LOW)
        assert await notifier.apply_operating_mode(OperatingMode.STOPPED) == 1
        assert not notifier.is_active("Orto", NotificationKind.BATTERY_LOW)

    @pytest.mark.asyncio
    async def test_back_to_active_lets_everything_through(self, notifier):
        await notifier.apply_operating_mode(OperatingMode.STOPPED)
        await notifier.apply_operating_mode(OperatingMode.ACTIVE)
        assert await notifier.notify(None, NotificationKind.WATER_ME_NOW, context={"zones": "x"}) is True


# ── The controller: no commands, no expectations ────────────────────


class TestControllerStopped:
    @pytest.fixture
    async def stopped(self, controller):
        await controller.async_set_operating_mode(OperatingMode.STOPPED)
        return controller

    @pytest.mark.asyncio
    async def test_irrigate_zone_is_refused_with_a_message(self, stopped, hass_mock):
        hass_mock.async_create_task = MagicMock()
        with pytest.raises(ServiceValidationError) as refused:
            await stopped._handle_irrigate_zone(_call({"zone_name": "Orto"}))
        assert refused.value.translation_key == "environment_stopped"
        assert refused.value.translation_placeholders == {"zone": "Orto"}
        hass_mock.async_create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_valve_test_is_refused(self, stopped):
        with pytest.raises(ServiceValidationError):
            await stopped._handle_test_valve(_call({"zone_name": "Orto"}))

    @pytest.mark.asyncio
    async def test_irrigate_all_steps_aside_without_failing_other_entries(self, stopped, hass_mock):
        hass_mock.async_create_task = MagicMock()
        await stopped._handle_irrigate_all(_call({}))
        hass_mock.async_create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_scheduled_hour_opens_nothing(self, stopped, zone_orto, hass_mock):
        zone_orto._zone_deficit = 30.0
        hass_mock.async_create_task = MagicMock()
        stopped._make_scheduled_handler("Orto")(MagicMock())
        hass_mock.async_create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_reachability_is_not_judged(self, stopped):
        stopped._reachability = MagicMock()
        await stopped._check_reachability()
        stopped._reachability.observe.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_amber_chip_for_a_valve_in_the_shed(self, controller, zone_orto, hass_mock):
        hass_mock.states.get = MagicMock(return_value=MagicMock(state="unavailable"))
        zone_orto._valve_seen = True  # past the startup grace
        assert zone_orto.valve_reachable is False  # active: an absent valve is a fault
        await controller.async_set_operating_mode(OperatingMode.STOPPED)
        assert zone_orto.valve_reachable is None

    @pytest.mark.asyncio
    async def test_a_valve_opened_by_hand_is_recorded_but_never_auto_closed(self, stopped, zone_orto, hass_mock):
        hass_mock.async_create_task = MagicMock()
        stopped._on_valve_state_change(_event("switch.valve_orto", "off", "on"))
        assert "switch.valve_orto" in stopped._manual_valve_open
        assert zone_orto.is_irrigating
        hass_mock.async_create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_entering_stopped_closes_only_the_valve_in_use(self, controller):
        controller._running = True
        controller._active_valve = "switch.valve_orto"
        controller._close_valve = AsyncMock(return_value=True)
        await controller.async_set_operating_mode(OperatingMode.STOPPED)
        controller._close_valve.assert_awaited_once_with("switch.valve_orto")
        assert controller._stop_requested

    @pytest.mark.asyncio
    async def test_the_mode_is_recorded_on_the_site(self, controller, di_sensor):
        await controller.async_set_operating_mode("away")
        assert di_sensor.environment.operating_mode is OperatingMode.AWAY

    @pytest.mark.asyncio
    async def test_back_to_active_waters_again(self, stopped, zone_orto, hass_mock):
        await stopped.async_set_operating_mode(OperatingMode.ACTIVE)
        zone_orto._zone_deficit = 30.0
        hass_mock.async_create_task = MagicMock(return_value=MagicMock())
        stopped._make_scheduled_handler("Orto")(MagicMock())
        hass_mock.async_create_task.assert_called_once()


# ── The select: restored, and found whichever platform comes first ──


class TestOperatingModeSelect:
    @pytest.fixture
    def hass(self):
        hass = MagicMock()
        hass.data = {}
        return hass

    @pytest.mark.asyncio
    async def test_a_restart_in_winter_stays_stopped(self, hass):
        select = OperatingModeSelect(hass, "entry1")
        select.async_get_last_state = AsyncMock(return_value=MagicMock(state="stopped"))
        await select.async_added_to_hass()
        assert select._attr_current_option == "stopped"

    @pytest.mark.asyncio
    async def test_an_unknown_restored_state_falls_back_to_active(self, hass):
        select = OperatingModeSelect(hass, "entry1")
        select.async_get_last_state = AsyncMock(return_value=MagicMock(state="unavailable"))
        await select.async_added_to_hass()
        assert select._attr_current_option == "active"

    @pytest.mark.asyncio
    async def test_before_the_controller_exists_the_mode_waits_for_it(self, hass):
        select = OperatingModeSelect(hass, "entry1")
        await select.async_select_option("away")
        assert hass.data["never_dry"][mode_key("entry1")] is OperatingMode.AWAY

    @pytest.mark.asyncio
    async def test_with_the_controller_loaded_the_mode_applies_at_once(self, hass):
        controller = MagicMock()
        controller.async_set_operating_mode = AsyncMock()
        hass.data["never_dry"] = {"_controller_entry1": controller}
        select = OperatingModeSelect(hass, "entry1")
        await select.async_select_option("stopped")
        controller.async_set_operating_mode.assert_awaited_once_with(OperatingMode.STOPPED)
