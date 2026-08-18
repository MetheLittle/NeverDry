"""Collecting the silence that ``environment.judge_silence`` knows how to judge.

The criterion has been written and tested for a while; what was missing is the
measurement it consumes. This module is that half, and nothing more: it gathers
how long each valve has been quiet and hands the fleet to the judge.

Two decisions carry it, both from ``docs/design/valve-reachability.md``.

**The device, not the entity.** A valve entity reports rarely — a switch that
nobody flips is silent for days without anything being wrong. But the physical
device usually carries a dozen entities, and *any* of them reporting proves it
is on the mesh. So the silence is measured over the union of the device's
entities, reached through the registries, and the members are never inspected:
we are not hunting for "the battery" or "the link quality". Where the union
cannot be derived — a template switch with no device — it degrades to the
configured entity alone: less signal, no error.

**Silence that ended is what normal looks like.** The floor comes from observed
cadence, and the only honest sample of "this much quiet happened and was fine"
is a silence that a device broke by speaking again. So each tick records how
long a device had been quiet, and when it finally reports, that peak becomes an
interval the floor can be built from. Nothing is assumed about the mesh's
rhythm; it is learned from the mesh.

``last_reported`` is the raw material because it is the one signal present on
every entity of every integration with nothing to enable — it moves on every
state write, whether or not the value changed.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .environment import Reachability, SilenceVerdict, judge_fleet, silence_floor

_LOGGER = logging.getLogger(__name__)

#: How many ended silences to keep per device for deriving the floor. The floor
#: is a high quantile, so it needs enough samples to have a tail at all.
INTERVAL_HISTORY: int = 50


@dataclass
class _DeviceWatch:
    """What we remember about one valve's device between ticks."""

    #: Entity ids whose reports count as "this device spoke".
    entities: tuple[str, ...] = ()
    #: The freshest ``last_reported`` seen so far, as a timestamp.
    last_report_ts: float | None = None
    #: Longest silence observed in the current quiet stretch.
    peak_silence_s: float = 0.0
    #: Silences that ended — the fleet's observed cadence.
    ended_silences: deque[float] = field(default_factory=lambda: deque(maxlen=INTERVAL_HISTORY))


class FleetSilenceWatch:
    """Measures per-valve silence across a fleet and judges it as a whole.

    Judging is delegated to :func:`environment.judge_fleet`, which leaves each
    valve out of its own reference. This class only answers "how long has each
    one been quiet", which is the part that needs Home Assistant.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._watches: dict[str, _DeviceWatch] = {}

    def _entities_for(self, valve_entity_id: str) -> tuple[str, ...]:
        """Every entity of the valve's device, or just the valve if it has none.

        Resolved once and cached: registry lookups are cheap but not free, and
        a device's entity set does not change between ticks in any way that
        matters here.
        """
        try:
            ent_reg = er.async_get(self._hass)
            entry = ent_reg.async_get(valve_entity_id)
            if entry is None or entry.device_id is None:
                return (valve_entity_id,)
            same_device = er.async_entries_for_device(ent_reg, entry.device_id, include_disabled_entities=False)
            ids = tuple(e.entity_id for e in same_device) or (valve_entity_id,)
            return ids
        except Exception:  # a registry hiccup must not silence the watch
            _LOGGER.debug("Could not resolve the device of '%s'", valve_entity_id, exc_info=True)
            return (valve_entity_id,)

    def _freshest_report(self, entities: tuple[str, ...]) -> float | None:
        """Most recent ``last_reported`` across ``entities``, as a timestamp."""
        newest: float | None = None
        for entity_id in entities:
            state = self._hass.states.get(entity_id)
            if state is None:
                continue
            reported = getattr(state, "last_reported", None) or state.last_updated
            if reported is None:
                continue
            ts = reported.timestamp()
            if newest is None or ts > newest:
                newest = ts
        return newest

    def observe(self, valves: dict[str, str]) -> dict[str, SilenceVerdict]:
        """Take one reading of the fleet and judge it.

        ``valves`` maps a key (the zone name) to its valve entity id. Returns a
        verdict per key; keys whose silence cannot be measured are omitted
        rather than guessed at.
        """
        # Epoch seconds on both sides: `last_reported` is timezone-aware and
        # its timestamp() is epoch too, so the difference needs no timezone.
        now = time.time()
        silences: dict[str, float] = {}
        intervals: list[float] = []

        for key, valve_entity_id in valves.items():
            watch = self._watches.get(key)
            if watch is None or not watch.entities:
                watch = _DeviceWatch(entities=self._entities_for(valve_entity_id))
                self._watches[key] = watch

            freshest = self._freshest_report(watch.entities)
            if freshest is None:
                continue

            if watch.last_report_ts is not None and freshest > watch.last_report_ts:
                # The device spoke since the last tick: the quiet stretch that
                # just ended is a sample of silence that turned out to be fine.
                if watch.peak_silence_s > 0:
                    watch.ended_silences.append(watch.peak_silence_s)
                watch.peak_silence_s = 0.0
            watch.last_report_ts = freshest

            silence = max(0.0, now - freshest)
            watch.peak_silence_s = max(watch.peak_silence_s, silence)
            silences[key] = silence
            intervals.extend(watch.ended_silences)

        if not silences:
            return {}

        floor = silence_floor(intervals)
        if floor is None:
            # Nothing has ever been observed to end, so there is no evidence of
            # what "normally quiet" looks like. Judging now would be guessing.
            return {key: SilenceVerdict(Reachability.UNKNOWN, s) for key, s in silences.items()}

        return judge_fleet(silences, floor_s=floor)

    def diagnostics(self) -> dict:
        """What the watch has learned, for the diagnostics bundle."""
        return {
            key: {
                "entities_watched": len(w.entities),
                "silence_samples": len(w.ended_silences),
                "peak_silence_s": round(w.peak_silence_s, 1),
            }
            for key, w in self._watches.items()
        }
