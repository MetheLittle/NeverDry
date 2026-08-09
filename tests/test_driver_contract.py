"""Tests for the driver's contract surface — the parts that need no HA runtime.

``driver.py`` is the one scaffold that is Home Assistant-coupled, so it splits
into two halves. This file covers the half that is pure: the entity adapter, the
delivery contract, and the valve-less ManualActuator. The async half — the
Driver base with its watchdog, adaptive timeouts and liveness probe — needs the
mocked-hass harness of ``test_valve_operator.py`` and is not covered here.

The adapter is worth pinning precisely: it is the single place that knows a
``switch.*`` opens with ``switch.turn_on`` while a ``valve.*`` opens with
``valve.open_valve``, and it is what makes GH #94 (Orbit B-hyve and friends)
additive rather than a migration.
"""

import inspect

import pytest
from never_dry.driver import (
    DeliveryMode,
    DeliveryQuality,
    DeliveryResult,
    Driver,
    EntityDomain,
    ManualActuator,
    MasterDriver,
    OperationResult,
    OperationStatus,
    ValveCommandAdapter,
    ZoneDriver,
)


class TestValveCommandAdapter:
    """Domain is derived from the entity_id prefix — no config, no migration."""

    def test_switch_entities_map_to_the_switch_domain(self):
        assert ValveCommandAdapter("switch.garden").domain is EntityDomain.SWITCH

    def test_valve_entities_map_to_the_valve_domain(self):
        assert ValveCommandAdapter("valve.bhyve_timer").domain is EntityDomain.VALVE

    def test_anything_else_falls_back_to_switch(self):
        """A pump is not a domain: HA sees a relay, so it must take the switch branch."""
        assert ValveCommandAdapter("light.weird").domain is EntityDomain.SWITCH
        assert ValveCommandAdapter("no_dot_at_all").domain is EntityDomain.SWITCH

    def test_switch_commands(self):
        adapter = ValveCommandAdapter("switch.garden")
        assert adapter.command(on=True) == ("switch", "turn_on")
        assert adapter.command(on=False) == ("switch", "turn_off")

    def test_valve_commands(self):
        adapter = ValveCommandAdapter("valve.bhyve_timer")
        assert adapter.command(on=True) == ("valve", "open_valve")
        assert adapter.command(on=False) == ("valve", "close_valve")

    @pytest.mark.parametrize("raw", ["on", "open"])
    def test_open_states_collapse_to_on(self, raw):
        assert ValveCommandAdapter.interpret_state(raw) == "on"

    @pytest.mark.parametrize("raw", ["off", "closed"])
    def test_closed_states_collapse_to_off(self, raw):
        assert ValveCommandAdapter.interpret_state(raw) == "off"

    @pytest.mark.parametrize("raw", ["opening", "closing"])
    def test_moving_states_are_transitional(self, raw):
        """A valve mid-travel must emit no FSM observation until it settles."""
        assert ValveCommandAdapter.interpret_state(raw) == "transitional"

    def test_missing_state_is_unavailable(self):
        assert ValveCommandAdapter.interpret_state(None) == "unavailable"
        assert ValveCommandAdapter.interpret_state("unavailable") == "unavailable"

    def test_unknown_is_kept_distinct_from_unavailable(self):
        assert ValveCommandAdapter.interpret_state("unknown") == "unknown"

    def test_an_unrecognised_state_is_not_silently_open(self):
        """Defaulting a surprise state to "on" would leave a valve believed open."""
        assert ValveCommandAdapter.interpret_state("banana") == "unknown"


class TestDeliveryResult:
    """The round trip: how much water, and how truthful the figure is."""

    def test_carries_the_requested_and_delivered_figures(self):
        result = DeliveryResult(9.5, DeliveryQuality.MEASURED, 120.0, 10.0)
        assert result.liters_delivered == 9.5
        assert result.requested_liters == 10.0
        assert result.elapsed_s == 120.0

    def test_revise_replaces_a_late_measurement(self):
        """A backend that measures late — Hydrawise — corrects an estimate afterwards."""
        estimated = DeliveryResult(10.0, DeliveryQuality.ESTIMATED, 120.0, 10.0)
        revised = estimated.revise(8.7)
        assert revised.liters_delivered == 8.7
        assert revised.quality is DeliveryQuality.MEASURED

    def test_revise_returns_a_copy(self):
        estimated = DeliveryResult(10.0, DeliveryQuality.ESTIMATED, 120.0, 10.0)
        estimated.revise(8.7)
        assert estimated.liters_delivered == 10.0

    def test_revise_can_state_a_different_quality(self):
        partial = DeliveryResult(4.0, DeliveryQuality.ESTIMATED, 60.0, 10.0)
        assert partial.revise(4.2, DeliveryQuality.PARTIAL).quality is DeliveryQuality.PARTIAL

    def test_satisfies_the_zone_delivery_protocol(self):
        """The seam that keeps zone.py free of Home Assistant."""
        from never_dry.zone import Delivery

        assert isinstance(DeliveryResult(1.0, DeliveryQuality.MEASURED, 1.0, 1.0), Delivery)


class TestOperationResult:
    def test_carries_its_status(self):
        assert OperationResult(OperationStatus.OK).status is OperationStatus.OK

    def test_statuses_are_distinct(self):
        assert len({s.value for s in OperationStatus}) == len(list(OperationStatus))


class TestManualActuator:
    """A *how* with no hardware: the watering can, for house plants."""

    def test_starts_idle(self):
        actuator = ManualActuator(name="Ficus")
        assert not actuator.is_pending
        assert actuator.pending_liters == 0.0
        assert actuator.name == "Ficus"

    def test_requesting_irrigation_opens_an_alert_and_delivers_nothing_yet(self):
        actuator = ManualActuator(name="Ficus")
        result = actuator.request_irrigation(1.5, deficit_mm=12.0)
        assert actuator.is_pending
        assert actuator.pending_liters == 1.5
        assert result.liters_delivered == 0.0
        assert result.quality is DeliveryQuality.DELAYED
        assert result.requested_liters == 1.5

    def test_the_alert_callback_receives_the_dose(self):
        seen = []

        def record(name, liters, deficit):
            seen.append((name, liters, deficit))

        actuator = ManualActuator(name="Ficus", on_alert=record)
        actuator.request_irrigation(1.5, deficit_mm=12.0)
        assert seen == [("Ficus", 1.5, 12.0)]

    def test_marking_irrigated_declares_the_volume_and_clears_the_alert(self):
        actuator = ManualActuator(name="Ficus")
        actuator.request_irrigation(1.5)
        result = actuator.mark_irrigated()
        assert not actuator.is_pending
        assert result.quality is DeliveryQuality.DECLARED
        assert result.liters_delivered == 1.5

    def test_marking_irrigated_accepts_a_different_amount(self):
        """The user poured what they poured, not what was asked."""
        actuator = ManualActuator(name="Ficus")
        actuator.request_irrigation(1.5)
        assert actuator.mark_irrigated(0.8).liters_delivered == 0.8

    def test_the_clear_callback_fires_on_confirmation(self):
        cleared = []
        actuator = ManualActuator(name="Ficus", on_clear=lambda n: cleared.append(n))
        actuator.request_irrigation(1.5)
        actuator.mark_irrigated()
        assert cleared == ["Ficus"]

    def test_is_not_a_driver(self):
        """Deliberate: no entity, no FSM, no safety layers to inherit.

        It shares only the DeliveryResult contract, which is what lets the Zone
        settle identically whether the water came from a valve or a watering can.
        """
        assert not issubclass(ManualActuator, Driver)


class TestDriverFamily:
    def test_both_drivers_specialize_the_common_base(self):
        assert issubclass(ZoneDriver, Driver)
        assert issubclass(MasterDriver, Driver)

    def test_the_base_is_abstract(self):
        """`role` is the hook that forces a specialization to declare itself.

        Asserted on ``__abstractmethods__`` rather than by calling ``Driver()``:
        the constructor takes required arguments, so an empty call raises
        TypeError for the wrong reason and the test would pass even if the base
        stopped being abstract.
        """
        assert inspect.isabstract(Driver)
        assert "role" in Driver.__abstractmethods__

    def test_delivery_modes_are_distinct(self):
        assert len({m.value for m in DeliveryMode}) == len(list(DeliveryMode))
