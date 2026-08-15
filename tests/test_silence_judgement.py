"""Judging a quiet valve against its siblings.

The rule has to survive the fleet sizes and the failure shapes that actually
occur, not just the happy one. Each test below is a case that would otherwise
be found in someone's garden.
"""

from __future__ import annotations

import pytest
from never_dry.environment import (
    Reachability,
    judge_fleet,
    judge_silence,
    silence_floor,
)

MINUTE = 60.0
HOUR = 3600.0


# ── The case it exists for ────────────────────────────────────────────────


class TestOneValveGoneQuiet:
    def test_the_dead_one_is_singled_out(self):
        verdict = judge_silence(3 * HOUR, [4 * MINUTE, 4 * MINUTE, 5 * MINUTE], floor_s=30 * MINUTE)
        assert verdict.status is Reachability.SILENT

    def test_the_verdict_carries_the_numbers_it_used(self):
        """So the warning can explain itself instead of just asserting."""
        verdict = judge_silence(3 * HOUR, [4 * MINUTE, 4 * MINUTE, 5 * MINUTE], floor_s=30 * MINUTE)
        assert verdict.reference_s == 4 * MINUTE
        assert verdict.threshold_s == 30 * MINUTE  # the floor bites, not the factor
        assert verdict.silence_s == 3 * HOUR

    def test_the_healthy_siblings_are_not(self):
        fleet = {"pino": 3 * HOUR, "ortensia": 4 * MINUTE, "melograno": 4 * MINUTE, "melino": 5 * MINUTE}
        verdicts = judge_fleet(fleet, floor_s=30 * MINUTE)
        assert verdicts["pino"].is_silent
        assert not any(verdicts[z].is_silent for z in ("ortensia", "melograno", "melino"))


# ── Leave-one-out is what makes small fleets work ─────────────────────────


class TestTheSubjectIsLeftOutOfItsOwnReference:
    def test_two_valves_one_dead(self):
        """Including itself, the dead valve drags the median up and acquits itself.

        median([5 min, 4 h]) is about two hours, and four hours does not exceed
        twice that. Leaving it out, the reference is five minutes and the
        finding is obvious.
        """
        verdicts = judge_fleet({"dead": 4 * HOUR, "alive": 5 * MINUTE}, floor_s=30 * MINUTE, min_peers=1)
        assert verdicts["dead"].is_silent
        assert verdicts["dead"].reference_s == 5 * MINUTE

    def test_a_single_valve_cannot_be_judged(self):
        verdicts = judge_fleet({"only": 6 * HOUR}, floor_s=30 * MINUTE)
        assert verdicts["only"].status is Reachability.UNKNOWN

    def test_too_few_peers_is_unknown_not_fine(self):
        """The distinction the whole enum exists for."""
        verdict = judge_silence(6 * HOUR, [5 * MINUTE], floor_s=30 * MINUTE, min_peers=2)
        assert verdict.status is Reachability.UNKNOWN
        assert not verdict.is_silent
        assert verdict.reference_s is None


# ── The floor stops noise from becoming alarms ────────────────────────────


class TestTheFloor:
    def test_a_tiny_reference_does_not_make_jitter_a_fault(self):
        """Right after a restart everything is seconds old; twice that is nothing."""
        verdict = judge_silence(90.0, [30.0, 30.0, 30.0], floor_s=30 * MINUTE)
        assert verdict.status is Reachability.LIVE

    def test_the_factor_bites_when_the_fleet_is_slow(self):
        """With a genuinely slow mesh the factor, not the floor, decides."""
        verdict = judge_silence(5 * HOUR, [1 * HOUR, 1 * HOUR, 70 * MINUTE], floor_s=30 * MINUTE)
        assert verdict.status is Reachability.SILENT
        assert verdict.threshold_s == 2 * HOUR

    def test_exactly_at_the_threshold_is_not_a_fault(self):
        verdict = judge_silence(30 * MINUTE, [1.0, 1.0, 1.0], floor_s=30 * MINUTE)
        assert verdict.status is Reachability.LIVE


# ── Fleet-wide situations ─────────────────────────────────────────────────


class TestWholeFleetSituations:
    def test_everything_fresh_after_a_restart_accuses_nobody(self):
        """The startup false positive, answered by the shape of the rule itself."""
        fleet = dict.fromkeys(("a", "b", "c", "d"), 20.0)
        assert not any(v.is_silent for v in judge_fleet(fleet, floor_s=30 * MINUTE).values())

    def test_the_whole_mesh_down_accuses_nobody(self):
        """Correct: it is not a fault of a valve, and the coordinator says so itself."""
        fleet = dict.fromkeys(("a", "b", "c", "d"), 9 * HOUR)
        assert not any(v.is_silent for v in judge_fleet(fleet, floor_s=30 * MINUTE).values())

    def test_two_dead_out_of_four_are_both_found(self):
        """The median holds while the dead are a minority."""
        fleet = {"a": 6 * HOUR, "b": 6 * HOUR, "c": 3 * MINUTE, "d": 4 * MINUTE}
        verdicts = judge_fleet(fleet, floor_s=30 * MINUTE, min_peers=2)
        assert verdicts["a"].is_silent and verdicts["b"].is_silent
        assert not verdicts["c"].is_silent and not verdicts["d"].is_silent

    def test_a_majority_dead_hides_them(self):
        """An honest limit, written down rather than discovered later.

        Once the quiet ones are the majority they become the reference, and the
        rule reports the healthy minority as normal and the rest as normal too.
        A relative measure cannot do better; the absolute floor is what would
        have to catch this, if it were set low enough to.
        """
        fleet = {"a": 6 * HOUR, "b": 6 * HOUR, "c": 6 * HOUR, "d": 4 * MINUTE}
        verdicts = judge_fleet(fleet, floor_s=30 * MINUTE)
        assert not any(v.is_silent for v in verdicts.values())


# ── Deriving the floor from cadence ───────────────────────────────────────


class TestSilenceFloor:
    def test_scales_with_how_often_the_fleet_speaks(self):
        assert silence_floor([10 * MINUTE, 10 * MINUTE, 12 * MINUTE]) == 30 * MINUTE

    def test_a_chatty_mesh_gets_a_small_floor(self):
        assert silence_floor([30.0, 30.0, 30.0]) == 90.0

    def test_no_observation_means_no_derived_floor(self):
        assert silence_floor([]) is None

    def test_zero_and_negative_intervals_are_ignored(self):
        """A restart can produce a zero delta; it says nothing about cadence."""
        assert silence_floor([0.0, -5.0, 10 * MINUTE, 10 * MINUTE]) == 30 * MINUTE

    def test_only_unusable_values_means_none(self):
        assert silence_floor([0.0, 0.0]) is None


# ── The numbers from the field ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("silence_min", "expected"),
    [
        (23.4, Reachability.LIVE),  # what the Pino showed: not yet evidence
        (180.0, Reachability.SILENT),  # three hours: it is
    ],
)
def test_the_pino_reading(silence_min, expected):
    """Taken from the live instance: three siblings at 2.5 minutes.

    23 minutes of quiet is not proof, and the rule must not call it proof —
    that reading came from a manual test, not from a dead valve.
    """
    peers = [2.5 * MINUTE, 2.5 * MINUTE, 2.5 * MINUTE]
    assert judge_silence(silence_min * MINUTE, peers, floor_s=30 * MINUTE).status is expected
