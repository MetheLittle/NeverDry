# Valve reachability — noticing a valve that has stopped answering

**Status:** Proposed (RFC)
Implementation: `custom_components/never_dry/environment.py`, `sensor.py` (`valve_reachable`)
Tests: `tests/test_silence_judgement.py`, `tests/test_valve_reachability.py`

## The failure this is about

A battery-powered valve runs out of charge in the middle of the season. It stops
answering. The zone stops being watered. And **nothing says so**:

- the switch entity keeps reporting a perfectly ordinary `off`;
- the battery sensor keeps showing its last reading, which was fine;
- the coordinator does not mark it unavailable, because the availability timeout
  it applies to a battery device is measured in *days* — a sleeping valve is
  supposed to be quiet;
- the zone's deficit keeps rising, which looks exactly like a dry spell.

The plants find out first. This was reproduced on a live installation
(Zigbee2MQTT 2.13, availability enabled): a valve known to be off the mesh was
indistinguishable from three healthy siblings on every signal Home Assistant
exposes.

There is a second, milder version of the same failure: the user presses
*Irrigate* and, apparently, nothing happens. Underneath, the operator retries six
times over about fifty seconds and then blocks the zone. That one is now visible
too — see *Two signals* below.

## Why one valve cannot be asked

Every direct question has an unusable answer:

| Question | Why it fails |
|---|---|
| Is the entity `unavailable`? | No: it reports a stale `off` indefinitely. |
| Has it been quiet for more than N minutes? | Every mesh has its own cadence; no single N is right for a chatty valve and a sleepy one, and asking the user to pick it is asking them to guess. |
| What does the recorder say about its cadence? | Nothing usable. The recorder stores state *changes*; a device re-reporting the same value writes no row. Measured on the live instance: 15–20 points in 24 h, median gap 0.9 min — all of them clustered around our own restarts, with an 18-hour hole in between. |
| Is `last_seen` set? | Zigbee2MQTT 2.x publishes it to MQTT but nothing surfaces it in Home Assistant: the entities carry no raw payload attributes, and discovery creates no `last_seen` sensor. It would also require every user to change a Zigbee2MQTT default, would be undetectable when they had not, and would leave ZHA, Matter and Wi-Fi valves with nothing. |

What *is* available on every entity, in every integration, with nothing to
enable: **`last_reported`** — when Home Assistant last received a state write,
whether or not the value changed. That is the raw material.

## The rule

Ask a different question. Not *has this valve been quiet too long*, but **is this
valve unusually quiet compared to its siblings**.

```
reference = median(silence of the OTHER valves)
spread    = MAD(silence of the OTHER valves)
threshold = max(reference + k·spread, floor)
silent    = silence > threshold
```

with `k = 3` and the floor derived from observed cadence, not set in minutes:
`floor = median(observed inter-report intervals) × 3`.

Three properties follow from the shape, without special cases:

- **It self-calibrates.** When the whole mesh goes quiet at night the reference
  moves with it, so nobody is accused.
- **A restart accuses nobody.** Everything is fresh together, so the reference is
  tiny and the floor holds the line. The startup false positive is answered by
  the rule rather than by an exception to it.
- **A coordinator outage accuses nobody.** All silences rise together. That is
  correct: it is not a fault *of a valve*, and the bridge reports itself.

### The subject is left out of its own reference

Not a detail. With two valves and the dead one included, the median sits halfway
between healthy and dead: the dead valve drags up its own reference and acquits
itself. Leaving it out keeps the reference honest at any fleet size, and it is
what makes the wild-sibling case work — a valve that has just rejoined after a
week away would otherwise blow the bar wide open and hide a real fault.

### Why the bar has two parts

They answer different questions, and both are needed.

`reference + k·MAD` asks *is this unusual for this fleet*. It widens on its own
when the fleet's cadence is genuinely irregular, which a flat multiplier cannot
do: a fleet reporting every 10 min ± 1 and one reporting every 10 min ± 8
deserve different bars.

The floor asks *is this unusual at all*. On a tight, freshly-restarted fleet the
MAD is zero and the bar would collapse onto the median, making ordinary jitter
look like a fault.

## Three values, not a boolean

`LIVE` / `SILENT` / `UNKNOWN`. "We cannot tell" is a real and frequent answer —
one zone configured, or a fleet too small to compare — and collapsing it into
"fine" is how a warning system loses its meaning. Absence of evidence is not
evidence of absence.

The verdict carries the numbers it was reached from (`reference_s`,
`threshold_s`) so the warning can explain itself: *"quiet for three hours while
the others last spoke four minutes ago"* is actionable; *"not responding"* alone
is not.

## Estimators considered

Measured against the same set of cases before choosing. ✅ = correct verdict.

| Case | Fixed threshold | Tukey `Q3 + 1.5·IQR` | `median + 3·MAD` |
|---|---|---|---|
| One dead of four (3 h / 1 h / 40 min) | depends on N | ✅ | ✅ |
| Two dead of four | depends on N | ❌ missed | ✅ |
| Three valves, one dead | depends on N | ❌ n < 4 | ✅ |
| Two valves, one dead | depends on N | ❌ n < 4 | ✅ |
| One sibling wild (rejoined after a week) | depends on N | ❌ missed | ✅ |
| All fresh after a restart | ❌ false positives | ✅ | ✅ |
| Whole mesh down | ❌ false positives | ✅ | ✅ |

**Tukey's fence** is the better-known and better-principled definition of an
outlier, and it was the preferred candidate. It lost on sample size: quartiles
over the three peers of a four-zone garden are interpolations between two
numbers, and the fence is undefined below four points. It is also the estimator
that the wild sibling defeats — the outlier inflates the IQR and the fence opens
wide enough to swallow the fault it was meant to catch. **Worth revisiting for
installations with ten or more zones**, where the sample supports it.

**Mean + k·σ** was rejected for the peer comparison for the same reason the mean
is always wrong here: the thing being detected *is* an outlier, so an estimator a
single wild value can inflate will hide it. The MAD is its robust counterpart and
keeps the intuition — level plus dispersion — intact.

The floor keeps `median × 3` rather than `median + k·MAD`, deliberately: it
answers "how long is quiet still normal", which is a multiple of the usual
cadence, not a level plus a spread. Symmetry for its own sake would make it
tighter than a single normal interval.

## Honest limits

- **A majority quiet hides them all.** Once the silent valves are more than half
  the fleet they *are* the reference. A relative measure cannot do better; only
  an absolute floor low enough to be noisy would catch it. Covered by a test so
  the limit stays visible.
- **Fleets below three valves** fall back to the floor alone, and a single-zone
  installation returns `UNKNOWN` for ever. This is the honest answer, not a
  degradation: with nothing to compare against there is no measurement.
- **`last_reported` is not strictly "the device spoke".** It also advances when
  a coordinator republishes cached state on restart. The relative rule absorbs
  this: everyone resets together.

## Two signals, two budgets

Reachability is reported through two channels on purpose, with different costs
to the user.

**Ambient** — the amber warning on the zone card. Always visible, interrupts
nothing, costs nothing to be wrong about. This is where a suspicion belongs.

**Interruptive** — a notification. Rare by design, because *an alert repeated
about a fault nobody is fixing is how the alert that matters gets ignored*. The
policy:

- raised **once per episode** (`UNREACHABLE_PASSIVE`); `ValveNotifier`
  deduplicates on `(zone, kind)`, so a persisting fault does not re-notify;
- **cleared automatically on recovery**, so it never becomes a stale message the
  user has to dismiss;
- spoken again only when the silence **actually costs a watering** — a scheduled
  run skipped because the valve will not answer (`UNREACHABLE_AT_IRRIGATION`).
  One message per missed watering: bounded, and proportional to the harm.

Escalation is by *consequence*, not by elapsed time. A valve nobody is going to
fix until the weekend should not generate a reminder every hour; it should
generate one when a watering is actually lost.

## Where the runtime number comes from

`judge_fleet()` takes a mapping of actuator → seconds of silence. The number is
the **driver's** to supply: it is the only layer that knows which entity backs a
given actuator and when that entity last reported. The judgement is the
**site's**, because no valve can judge itself — the comparison *is* the
measurement.

That seam is why the rule lives in `environment.py` and is pure: it can be
exercised over lists of numbers, including every failure shape above, without a
Home Assistant runtime. Wiring it belongs with the driver work, alongside
`Driver.async_ping`, which will finally have a source worth polling.
