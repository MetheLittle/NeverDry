# The scheduler: what has been asked for

**Status:** collecting. Nothing here is decided, and the list below is
deliberately **not** reconciled.

## Why this document exists

Six open issues, from five different people who do not know each other, all ask
for something the integration does not have: an object that decides **when** a
zone waters, **in what order** zones go, and **whether to stop** one that is
running.

They arrived separately, over months, each framed as a feature. Read together
they are one gap. That is the only claim this document makes today.

The code says the same thing. `scheduler.py` exists and holds a `Scheduler` with
three methods. One of them, `evaluate_reactive`, is wired: the controller calls
it when the deficit crosses a threshold. The other two are not.
`evaluate_scheduled` has no callers, so the daily-time path does not go through
the scheduler at all, and `next_eligible` has none either, with a comment saying
it deliberately does not implement a queue because a queue has memory of what is
waiting. So the object is half built, and every request below runs into the
missing half.

## What is already built, and what is not

This is not a green field, and the distinction matters for what the discussion
is for. Everything **under** a run is in place and in production:

- a `Scheduler` with a decision vocabulary already in use, `Decision`, `Trigger`
  and `SkipReason`, so a refusal has a reason rather than a silence;
- a domain `Zone` that owns its deficit and answers `needs_water` from the one
  number it is acting on, whether that came from the weather model or from the
  soil itself;
- a delivery contract: a run has a defined end and has to show evidence that
  water moved, with the valve state machine, the operator, the watchdog and the
  reachability checks underneath it;
- the water balance itself, four methods deep, now including a zone that reads
  its own probe.

What is missing is the layer **above** a run: ordering, waiting, preconditions,
and stopping something already under way. That layer has no home today, which is
why six requests that each need a piece of it have all stalled.

So the discussion is not about where to start. It is about settling what that
layer has to do, in the words of the people who need it, so it can be **wired**
rather than invented.

## What it does today

- **Reactive mode**: when a zone's deficit crosses its threshold, it waters. The
  only thing standing between two triggers is `MIN_SERVICE_INTERVAL_S`, ten
  seconds, which is a rate limit on service calls rather than a cooldown.
- **Scheduled mode**: at a set time of day, a zone waters regardless of the
  threshold. This is deliberate, and it does not go through `Scheduler`.
- **Order**: zones run in the order they were configured. There is no queue.
- **Overlap**: the controller refuses to start a zone while another is running,
  and that refusal is the whole of the concurrency policy.
- **Stopping**: a run ends when its delivery criterion is met or its safety
  timeout expires. Nothing outside the run can end it early except the emergency
  stop, which stops everything.

## The requests, uncoordinated

Listed one per issue, in the terms the person used. They contradict each other
in places and that is not resolved here. Reconciling them is the next step, and
it is a conversation rather than an edit.

### [#231](https://github.com/never-dry/NeverDry/issues/231): finish before sunrise (@sanderaernouts)

Reactive mode starts watering the moment the deficit crosses the threshold,
which can be the middle of the day. A front yard against a dark brick wall and
pavement loses most of that to evaporation. Asked for: configurable triggers for
*when* reactive mode is allowed to start, with a default of "sunrise minus the
expected duration", so the water goes in while the ground is cool and has time to
soak.

Notes that this may want NeverDry to emit an event an automation can hook, and
observes that a purely automation-based answer would not compose with
[#138](https://github.com/never-dry/NeverDry/issues/138).

### [#213](https://github.com/never-dry/NeverDry/issues/213): rain-aware interruption and manual suspension (maintainer)

Two things in one issue. Stopping a run that is under way because it has started
raining, and suspending the whole schedule by hand for a period, for example
while a lawn is being treated or a party is on.

### [#138](https://github.com/never-dry/NeverDry/issues/138): skip before rain (@rpatel3001)

Do not water when rain is coming. Distinct from the one above: this is a
decision taken **before** a run starts, not an interruption of one in progress.

Runs directly into [#222](https://github.com/never-dry/NeverDry/issues/222): the
water balance uses observed rain only, never forecast millimetres, and that is a
deliberate rule rather than a missing feature. Whatever answers this has to keep
forecast out of the deficit while letting it gate a decision.

### [#74](https://github.com/never-dry/NeverDry/issues/74): cycle and soak, queueing, well gate (@fpytloun)

The longest thread, eleven comments. Several distinct asks:

- **One valve at a time.** "In my setup only one valve/section should run at a
  time", so a second zone becoming due while one is running has to wait rather
  than be skipped. Today it is skipped.
- **Cycle and soak.** Split a dose into passes with a soak between them, so
  water goes in at the rate the ground can absorb rather than running off.
- **A well gate.** Do not draw when the source cannot supply, which is a
  precondition outside any single zone.
- **A Hydrawise adapter**, which is hardware rather than scheduling and is
  listed here only because the issue carries it.

### [#95](https://github.com/never-dry/NeverDry/issues/95): master pump or control valve (@MrEcosse)

A pump or a master valve that has to open before any zone and close after the
last one, with a linger so the line is not slammed. This is ordering and
coordination between zones rather than within one, and it is the request that
most clearly needs something above the individual run.

### [#214](https://github.com/never-dry/NeverDry/issues/214): manual run with a chosen duration

Run a zone for a stated number of minutes, rather than for the dose the model
computed. Adjacent rather than central: it is about what a run is, not when it
happens, but it lands on the same concurrency and stopping rules as everything
above.

## What each request already has to hold on to

None of the six starts from nothing, and this is the table to read first when
coming back to this file cold. Each request attaches to something that exists
and works today.

| request | what it attaches to |
|---|---|
| **#231** finish before sunrise | the zone already computes its expected duration. "Finish by sunrise" is that number subtracted from sunrise; the number exists |
| **#138** skip before rain | a refusal to water already carries a `SkipReason` and is published rather than silent. A forecast gate is one more reason in a list that already has a home |
| **#74** one valve at a time | the controller already refuses to start a zone while another runs. The refusal has to become a **wait**, which is the same decision with memory attached, and memory is exactly what `next_eligible` was written to avoid |
| **#74** cycle and soak | a run already has a defined end and must show that water moved. A pass is that run, repeated, with a gap |
| **#95** master pump | a valve is already opened, confirmed, watched and closed by `ValveOperator` and the driver. A master is another valve with an ordering rule around it, not a new kind of thing |
| **#213** interrupt a run | stopping early works, and since the delivery contract a run that ends short credits exactly what it delivered. What is missing is who may call it, and on what evidence |
| **#214** manual run of N minutes | in `estimated_flow` the duration **is** the dose. The mode exists; what is missing is asking for one without the model choosing the number |

Read down the right-hand column and the shape of the missing layer appears
without anyone designing it. It has to **wait**, **order**, **refuse with a
reason**, **repeat**, and **stop something already under way**. Five verbs, one
object, and four of the five already have a vocabulary somewhere in the code.

## What this document does not do

It does not propose a design, name an object, or say which of the requests above
survive contact with each other. Several of them pull in opposite directions.
Serialising zones makes "finish before sunrise" harder, because a queue has to
start earlier for the same finish. A well gate and a master pump are both
preconditions on a run but at different scopes. An interruption for rain and a
skip for rain read alike and are not the same mechanism.

Those are questions for the people who asked, and they are put to them in
**[discussion #239](https://github.com/never-dry/NeverDry/discussions/239)**,
which stays open. Anything decided there comes back here as an edit, with the
reasoning, so this file stops being a collection and becomes a design only when
somebody has actually agreed to it.
