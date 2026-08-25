"""Tests for the scheduler — the *when*, extracted from the controller callbacks.

These pin the rules that used to live inline inside two Home Assistant callbacks,
where they could not be exercised without a controller. The point of each test is
a rule someone could plausibly "simplify" away later, so each says why it holds.
"""

import pytest
from never_dry.scheduler import (
    ConcurrencyPolicy,
    Decision,
    Scheduler,
    SkipReason,
    Trigger,
)
from never_dry.zone import Zone


def make_zone(deficit_mm: float, *, threshold: float = 20.0, name: str = "lawn") -> Zone:
    """A zone sitting at a given deficit."""
    zone = Zone(name=name, area_m2=50.0, threshold_mm=threshold)
    zone.deficit = zone.deficit.with_value(deficit_mm)
    return zone


class TestScheduledMode:
    """The daily top-up. Its defining property is that it ignores the threshold."""

    def test_waters_below_threshold(self):
        """A schedule tops the zone up whatever the deficit — that is the point.

        Gating this on the reactive threshold would quietly turn every scheduled
        run into a reactive one, which is the bug AI-183 fixed. A zone at 5 mm
        with a 20 mm threshold must still water at its hour.
        """
        decision = Scheduler().evaluate_scheduled(make_zone(5.0, threshold=20.0), is_running=False)
        assert decision.should_irrigate
        assert decision.trigger is Trigger.SCHEDULED

    def test_skips_only_when_there_is_nothing_to_refill(self):
        decision = Scheduler().evaluate_scheduled(make_zone(0.0), is_running=False)
        assert not decision.should_irrigate
        assert decision.reason is SkipReason.NOTHING_TO_REFILL

    def test_negative_deficit_is_also_nothing_to_refill(self):
        """Deficit should never go below zero, but the guard must not be `== 0`."""
        decision = Scheduler().evaluate_scheduled(make_zone(-1.0), is_running=False)
        assert decision.reason is SkipReason.NOTHING_TO_REFILL

    def test_yields_to_a_running_irrigation(self):
        decision = Scheduler().evaluate_scheduled(make_zone(30.0), is_running=True)
        assert not decision.should_irrigate
        assert decision.reason is SkipReason.ALREADY_RUNNING


class TestReactiveMode:
    """Mode A: water once the deficit crosses the zone's own threshold."""

    def test_waters_at_the_threshold(self):
        """The comparison is `>=`, so exactly at the threshold must trigger."""
        decision = Scheduler().evaluate_reactive(make_zone(20.0, threshold=20.0), is_running=False)
        assert decision.should_irrigate
        assert decision.trigger is Trigger.REACTIVE

    def test_holds_below_the_threshold(self):
        decision = Scheduler().evaluate_reactive(make_zone(19.9, threshold=20.0), is_running=False)
        assert not decision.should_irrigate
        assert decision.reason is SkipReason.BELOW_THRESHOLD

    def test_yields_to_a_running_irrigation(self):
        decision = Scheduler().evaluate_reactive(make_zone(30.0), is_running=True)
        assert decision.reason is SkipReason.ALREADY_RUNNING

    def test_throttling_is_checked_after_the_threshold(self):
        """A throttled call that was below threshold reports the threshold.

        Order matters for the log: the user should be told the zone was not
        thirsty, not that we rate-limited a call we would have refused anyway.
        """
        sched = Scheduler()
        assert sched.evaluate_reactive(make_zone(5.0), is_running=False, is_throttled=True).reason is (
            SkipReason.BELOW_THRESHOLD
        )
        assert sched.evaluate_reactive(make_zone(30.0), is_running=False, is_throttled=True).reason is (
            SkipReason.THROTTLED
        )


class TestConcurrencyPolicy:
    """Serial is today's behaviour; naming it is what this object adds."""

    def test_serial_is_the_default(self):
        assert Scheduler().concurrency is ConcurrencyPolicy.SERIAL
        assert not Scheduler().allows_overlap

    @pytest.mark.parametrize("evaluate", ["evaluate_scheduled", "evaluate_reactive"])
    def test_parallel_policy_lets_a_zone_start_while_another_runs(self, evaluate):
        """Both entry points must honour the policy, not just one of them."""
        sched = Scheduler(concurrency=ConcurrencyPolicy.PARALLEL)
        decision = getattr(sched, evaluate)(make_zone(30.0), is_running=True)
        assert decision.should_irrigate


class TestNextEligible:
    """Ordering without memory — deliberately not a queue."""

    def test_picks_the_driest_zone(self):
        zones = [make_zone(25.0, name="lawn"), make_zone(40.0, name="roses"), make_zone(30.0, name="hedge")]
        assert Scheduler().next_eligible(zones, is_running=False).name == "roses"

    def test_ignores_zones_below_their_own_threshold(self):
        """Eligibility is per zone: a high deficit under a high threshold does not qualify."""
        zones = [make_zone(25.0, threshold=20.0, name="lawn"), make_zone(40.0, threshold=99.0, name="roses")]
        assert Scheduler().next_eligible(zones, is_running=False).name == "lawn"

    def test_returns_none_when_nothing_is_thirsty(self):
        assert Scheduler().next_eligible([make_zone(1.0)], is_running=False) is None

    def test_returns_none_while_serial_and_running(self):
        assert Scheduler().next_eligible([make_zone(40.0)], is_running=True) is None

    def test_parallel_policy_still_returns_a_zone_while_running(self):
        sched = Scheduler(concurrency=ConcurrencyPolicy.PARALLEL)
        assert sched.next_eligible([make_zone(40.0, name="roses")], is_running=True).name == "roses"

    def test_empty_list_is_not_an_error(self):
        assert Scheduler().next_eligible([], is_running=False) is None


class TestDecision:
    """The value object carries the reason, which is what reaches the user's log."""

    def test_go_carries_a_trigger_and_no_reason(self):
        decision = Decision.go(Trigger.MANUAL)
        assert decision.should_irrigate
        assert decision.trigger is Trigger.MANUAL
        assert decision.reason is None

    def test_skip_carries_a_reason_and_no_trigger(self):
        decision = Decision.skip(SkipReason.THROTTLED)
        assert not decision.should_irrigate
        assert decision.reason is SkipReason.THROTTLED
        assert decision.trigger is None
