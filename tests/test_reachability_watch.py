"""Tests for collecting the silence that the judge consumes.

The criterion itself is covered by test_silence_judgement.py. What is exercised
here is the half that was missing until a live installation showed why it
mattered: two valves off the Zigbee mesh, on 2026-08-18, that Home Assistant
still reported as an ordinary `off` and that every direct check called healthy.
"""

import time
from unittest.mock import MagicMock

from never_dry.environment import Reachability
from never_dry.reachability_watch import FleetSilenceWatch


def _hass(reports: dict[str, float], device_of: dict[str, str] | None = None):
    """A hass whose entities last reported at the given epoch seconds."""
    hass = MagicMock()

    def states_get(entity_id):
        if entity_id not in reports:
            return None
        state = MagicMock()
        state.last_reported = MagicMock()
        state.last_reported.timestamp = lambda e=entity_id: reports[e]
        return state

    hass.states.get = MagicMock(side_effect=states_get)
    return hass


def _watch_with_entities(hass, mapping: dict[str, tuple[str, ...]]) -> FleetSilenceWatch:
    """A watch with the device union stubbed, so registries stay out of the test."""
    watch = FleetSilenceWatch(hass)
    watch._entities_for = lambda valve, m=mapping: m[valve]
    return watch


class TestTheSilenceIsMeasuredOverTheWholeDevice:
    """A valve entity alone says little; the device's other entities say a lot."""

    def test_any_entity_of_the_device_counts_as_the_device_speaking(self):
        now = time.time()
        # The switch itself has been quiet for an hour, but a sibling entity of
        # the same device reported a minute ago: the device is plainly alive.
        hass = _hass({"switch.a": now - 3600, "sensor.a_battery": now - 60})
        watch = _watch_with_entities(hass, {"switch.a": ("switch.a", "sensor.a_battery")})

        watch.observe({"A": "switch.a"})

        assert watch._watches["A"].last_report_ts == now - 60

    def test_a_valve_with_no_device_falls_back_to_itself(self):
        now = time.time()
        hass = _hass({"switch.solo": now - 120})
        watch = FleetSilenceWatch(hass)
        watch._entities_for = lambda valve: (valve,)

        watch.observe({"A": "switch.solo", "B": "switch.solo"})

        assert watch._watches["A"].entities == ("switch.solo",)


class TestTheFloorIsLearnedFromSilencesThatEnded:
    """Only a silence a device broke by speaking proves that much quiet is fine."""

    def test_a_silence_that_ended_becomes_an_observed_interval(self):
        now = time.time()
        hass = _hass({"switch.a": now - 1800})
        watch = _watch_with_entities(hass, {"switch.a": ("switch.a",)})

        watch.observe({"A": "switch.a"})  # quiet for 30 min
        assert not watch._watches["A"].ended_silences

        # The device speaks: the stretch that just ended is a usable sample.
        hass.states.get = MagicMock(side_effect=lambda e: MagicMock(last_reported=MagicMock(timestamp=lambda: now)))
        watch.observe({"A": "switch.a"})

        assert len(watch._watches["A"].ended_silences) == 1
        assert watch._watches["A"].ended_silences[0] >= 1800
        # Reset to the fresh (near-zero) silence, not carrying the old peak.
        assert watch._watches["A"].peak_silence_s < 1.0

    def test_without_any_ended_silence_nothing_is_judged(self):
        """No evidence of normal cadence means no verdict, not a guess."""
        now = time.time()
        hass = _hass({"switch.a": now - 100, "switch.b": now - 99999})
        watch = _watch_with_entities(hass, {"switch.a": ("switch.a",), "switch.b": ("switch.b",)})

        verdicts = watch.observe({"A": "switch.a", "B": "switch.b"})

        assert {v.status for v in verdicts.values()} == {Reachability.UNKNOWN}


class TestTheFieldCase:
    """Two valves off the mesh, two alive — the shape seen on 2026-08-18."""

    def _fleet(self, now, quiet_s, live_s):
        reports = {
            "switch.dead1": now - quiet_s,
            "switch.dead2": now - quiet_s,
            "switch.live1": now - live_s,
            "switch.live2": now - live_s,
        }
        hass = _hass(reports)
        watch = _watch_with_entities(hass, {e: (e,) for e in reports})
        return hass, watch

    def test_the_quiet_pair_is_flagged_once_cadence_is_known(self):
        now = time.time()
        _hass_unused, watch = self._fleet(now, quiet_s=20 * 3600, live_s=120)
        valves = {
            "dead1": "switch.dead1",
            "dead2": "switch.dead2",
            "live1": "switch.live1",
            "live2": "switch.live2",
        }
        watch.observe(valves)  # first tick: builds the watches
        for key in valves:
            watch._watches[key].ended_silences.extend([60.0, 90.0, 120.0])

        verdicts = watch.observe(valves)

        assert verdicts["dead1"].status == Reachability.SILENT
        assert verdicts["dead2"].status == Reachability.SILENT
        assert verdicts["live1"].status == Reachability.LIVE
        assert verdicts["live2"].status == Reachability.LIVE

    def test_a_fleet_quiet_together_accuses_nobody(self):
        """Night, or a coordinator outage: everyone's silence rises at once."""
        now = time.time()
        _hass_unused, watch = self._fleet(now, quiet_s=8 * 3600, live_s=8 * 3600)
        valves = {
            "dead1": "switch.dead1",
            "dead2": "switch.dead2",
            "live1": "switch.live1",
            "live2": "switch.live2",
        }
        watch.observe(valves)
        for key in valves:
            watch._watches[key].ended_silences.extend([60.0, 90.0, 120.0])

        verdicts = watch.observe(valves)

        assert all(v.status != Reachability.SILENT for v in verdicts.values())
