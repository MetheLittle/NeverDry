# The scheduler: what has been asked for

**Status:** collecting. The requests below are deliberately **not** reconciled.
Three positions have been taken, and they are marked as such in a section of
their own; nothing else here is decided.

## Why this document exists

Six open issues, from five different people who do not know each other, all ask
for something the integration does not have: an object that decides **when** a
zone waters, **in what order** zones go, and **whether to stop** one that is
running.

They arrived separately, over months, each framed as a feature. Read together
they are one gap. That is the only claim this document makes today.

Three further requirements come from the maintainer's own working notes and have
no issue behind them. They were missing from the first version of this file, and
they ask for the same object.

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
- **Rain delay**: written into the model, and inert. `RainDelayPolicy` carries a
  probability threshold and a delay, `Environment` declares a rain-probability
  binding, and **nothing in production reads either**: the policy has no consumer
  anywhere, and the sensor is never offered in the configuration form. So the
  integration presents a rain delay as part of a site's policy, with no path from
  the sky to the decision. This changes what
  [#138](https://github.com/never-dry/NeverDry/issues/138) is asking for: not a
  new feature, but the wiring of one that is already promised.

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

**He has since answered the questions this raised**, on 2026-09-18, and his
answers are recorded where they bear rather than collected here: the objection to
truncation under the positions below, the opening edge of the window under
irrigability windows, and what to do when the window cannot hold every zone under
"An answer that came back".

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

## Three more, asked for by the maintainer

These have no issue behind them, and they were missing from the first version of
this document. They belong in the same list, because each needs the same missing
layer as the six above.

### Irrigability windows, at site level

Intervals within which any run may begin, declared for the installation rather
than for a zone. The reasons are all site-wide: municipal restrictions,
electricity or water tariffs, wind, evaporation, and simply not soaking a lawn
while people are standing on it.

They are a constraint, not a schedule. They say when watering is permitted,
never when it happens; what happens inside them is still decided by deficit and
threshold.

This is also the structural answer to
[#231](https://github.com/never-dry/NeverDry/issues/231): "finish before
sunrise" is a window whose end is sunrise, decided against the envelope rather
than as a special case for one request.

**Which edge carries the constraint** ([#231](https://github.com/never-dry/NeverDry/issues/231),
@sanderaernouts answering on 2026-09-18, maintainer position added the same day).
He asks for an optional "not before", defaulting to sunset, and explicitly not a
required one: with no opening edge the window degrades to "must be finished by
sunrise", which is all he asked for to begin with. He also restated the goal
while answering, and the restatement is worth keeping verbatim: **finish as much
watering as possible before the sun is out**, rather than finish before sunrise.
The first is a quantity to maximise, the second a boundary to respect, and only
the first explains why he would rather overrun than be cut short. His two answers
agree with each other: for his garden the window is a preference, not a limit.

Worth weighing them with his garden in view, which he gave unasked: **one zone on
a drip hose, finishing in under two hours**. Nothing about queueing, ordering or
contention touches him, so his reading of the edges is first hand and his reading
of what happens when zones compete is not.

A consideration that goes further than the option he offers, raised here for
objection and not as a decision: **the opening edge may be the one that carries
the constraint, leaving the closing edge as the exception.** A start rule is
decidable with what is known when it is applied: it is sunset, or it is not. A
finish rule is not, because enforcing it means either placing the start with the
expected duration, which is the estimate that made "sunrise minus duration"
unworkable as a trigger in the first place, or cutting at the edge, which is the
truncation the same person objects to above.
Reframing the trigger as a window therefore solved less than it appeared to; it
relocated the dependency on the estimate rather than removing it. An opening edge
needs no estimate at all.

Were that to hold, the closing edge would only earn its keep where a run can
genuinely fail to fit the night, and that case is narrow enough to name.
`scheduler.md` §11 puts a number on it: a zone of 3x10 minutes with 20-minute
soaks occupies 70 minutes of wall clock for 30 minutes of water. Four such zones
in series come to under five hours, and the interleaving corollary in that same section - during a
soak the pipe is free - gives most of it back. Reaching a night's length takes
many zones on heavy soil, run strictly in series, with no interleaving.

The other case is real but is not this request: a window that ends for a reason
of its own, a tariff or a municipal restriction, where stopping is the point.
That is the same distinction the truncation objection exposes, seen from the
other side.

### A freeze interlock, observed rather than declared

Below roughly 5 °C the valves must not be operated at all. Three things separate
it from a time window:

- it protects the hardware and not the plant, so it suppresses commands rather
  than irrigation: the reachability probe and the valve self-test can cycle a
  valve into damage exactly as a run can;
- it governs what NeverDry originates and nothing else. A valve opened from
  Zigbee2MQTT, from the entity, or by hand at the tap is observed and recorded,
  never blocked;
- the 5 °C is a margin chosen for everyone rather than a constant, which is why
  an override is a real question and not a refinement.

### A zone whose valve has been taken indoors

In the shoulder seasons a user may unscrew a valve and bring it inside. The zone
is then not actuable while remaining entirely outdoors: rain still falls on it,
and outdoor demand still describes it.

What is asked for is the houseplant treatment, and only for reporting and
actuation. The deficit stays real and stays visible, but it reads as advice to
water by hand rather than as a promise to water. A valve removed on purpose must
also stop raising the unreachable alarm, which is the difference between an
accurate state and a winter of false alarms.

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

## What the working note adds to the six

Alongside the requests, the reasoning has been accumulating in a draft note,
[`scheduler.md`](scheduler.md). It is referenced here because several of its
observations change how the requests above read, and they are easier to object to
now than after something is built.

- **Deferring is not skipping.** A skip is re-derivable: ask again in ten minutes
  with the same world and the answer is the same. A rain delay is not, because
  the right answer depends on how many times the zone has already been deferred.
  So a delay cannot be a fifth skip reason, and an unbounded deferral is
  indistinguishable from a skip. Its failure mode is silent: a garden that never
  waters because the forecast said 90% five mornings running, while the log reads
  reassuringly every time.
- **The counter resets when the zone stops needing water, however that
  happened.** Not by asking afterwards whether it actually rained, which would
  create a second source of truth about water the deficit has already accounted
  for.
- **Two brakes, not one.** A count of deferrals is blind to physics, so a deficit
  approaching its per-zone ceiling lapses the delay regardless of budget left.
- **Probability is not quantity.** A 90% chance of 0.2 mm refills nothing, so the
  predicate has three terms: enough rain, likely enough, soon enough. The horizon
  should equal the delay, because a high probability at 48 hours is a different
  decision smuggled in under the same number.
- **For parallel operation the policy says "may I" and the hydraulics say "can
  I".** Admission has to be evaluated when a zone asks, because capacity is
  consumed by whoever is already running. This is where the well gate of
  [#74](https://github.com/never-dry/NeverDry/issues/74) and the master pump of
  [#95](https://github.com/never-dry/NeverDry/issues/95) meet.
- **Cycle and soak has two different sizes.** A zone needing 3x10 minutes with
  20-minute soaks occupies 70 minutes of wall clock while drawing water for 30.
  Scheduling against the wrong one either overruns the window or wastes two
  thirds of the supply. The corollary is better news: during a soak the pipe is
  free, so serial operation can interleave zones in the gaps instead of standing
  idle.
- **Ordering needs no queue.** Recompute at each tick, driest first. Watering the
  driest zone lowers its deficit, so it stops winning: the ordering is
  self-balancing and there is nothing to store.
- **Whatever is built, the scheduler keeps no state about the world.** Timers,
  counters and the current time enter as arguments. That property is what lets
  every rule above be tested without a controller, a Home Assistant instance or a
  clock, and it is the first thing lost while adding clocks and counters. The one
  place memory is genuinely required is the deferral count, and that is memory
  about a *zone*, held on the zone.

## Positions already taken, and open to objection

Three of the tensions above were settled in the working note in August, before
this document existed. They are stated as positions rather than as decisions,
because the people they affect had not seen them.

- **A fixed hour outside every window: the site wins, and the zone is warned
  rather than refused.** The run is shifted to the first admissible time, not
  suppressed, and the warning names the effective time instead of merely
  reporting that the hour is not allowed.
- **A run may finish outside the window it started in, by stated policy.** For a
  cycle-and-soak run the cut falls on a segment boundary and never mid-segment:
  whole segments dropped still leave a valid pattern. Truncation was the default
  here, justified as the only option that cannot surprise someone who set a
  window for a reason.

  **@sanderaernouts objected, and the justification does not survive it**
  ([#231](https://github.com/never-dry/NeverDry/issues/231), 2026-09-18):
  finishing the zone matters more than finishing before sunrise, because the
  sunrise finish is an optimisation and nothing breaks by running late, while
  underwatering does break something. For his reason, truncation is precisely
  the option that surprises, and it surprises silently: a zone cut at 60% is
  short again tomorrow, cut again, with every run logged as policy.

  What the objection exposes is not a wrong default but a missing distinction.
  **The window does not record why it exists.** A tariff or a municipal
  restriction is a hard boundary, where overrunning costs money or breaks a rule.
  Soak time before sunrise is a preference, where overrunning costs nothing and
  stopping short costs the garden. One object, two opposite right answers, and
  today the object cannot tell them apart. Unreconciled: either the overrun
  policy becomes a setting, or the window declares its own kind and the policy
  follows from it.
- **The deferral budget belongs to the rain delay policy**, beside the
  probability threshold and the delay hours, while the counter belongs to the
  zone. The limit is a site rule; the count is a fact about one zone, and it has
  to survive a restart or every restart silently refills the budget.

Four questions are genuinely open, and three of them are better answered by the
people running the gardens than by anyone reading the code. They are section 14
of the working note, and they are named here because a question nobody can see is
a question nobody can answer:

- **Which forecast term ships first.** Probability only, which is today's model
  and the smallest change, but defers on drizzle; quantity only, arguably the
  better signal since a forecast amount already asserts that it will rain; or
  both at once, degrading to whatever a given weather integration supplies.
  Supporting a single term strands one group of users or the other.
- **What the forecast is bound to.** A `sensor.*` entity only, or a `weather.*`
  entity plus a horizon. The second covers more real installations and receives
  probability and quantity from the same call, but it is a service call rather
  than a state read, which is a different integration pattern. This one largely
  settles the question above, so it comes first.
- **What shape a freeze override takes.** A configurable threshold expresses
  tolerance by *degree*, "fine down to minus eight"; a declared fact about the
  installation expresses immunity by *kind*, self-draining lines or subsurface
  drip. They are not the same statement and neither can say the other. Whichever
  is chosen, the wording decides whether it is answered honestly: a control that
  reads "disable freeze protection" invites exactly the people who should not
  touch it.
- **Whether a suspended zone's deficit should be frozen.** It only bites for
  zones that get no rain credit at all, a patio or a greenhouse, which is
  narrower than "what happens over winter".

### An answer that came back

This section is where the document keeps its promise: what gets decided in the
discussion returns here as an edit, with the reasoning. So far one of the open
questions has been answered by the person it was put to, and the answer changed
its shape on the way.

**Contention, answered as a strategy rather than an answer**
([#231](https://github.com/never-dry/NeverDry/issues/231), @sanderaernouts,
2026-09-18). Asked whether the last zone should slip a night or every zone should
get a shortened run, he declined the binary and proposed a setting with four
options: `overshoot` (run past sunrise), `order by need` (largest deficit first),
`spread` (every zone gets something), and `longest since` (longest time since
last irrigation goes first).

The four are not the same kind of thing, and separating them is most of the work.
`overshoot` and `spread` answer what to do when the time does not fit: they are
capacity policies, and they only arise under contention. `order by need` and
`longest since` answer who goes first, which is a question even when there is
time for everyone. `order by need` is also not a proposal: it is what this
document already holds, so it reads as assent rather than as a new option.

**`longest since` is the one nobody had named**, and it is worth keeping. It is
the only criterion offered that does not consult the deficit, which makes it the
only one that can protect a zone the deficit never lets win: a high threshold or
a slow loss puts the same zone last every night, where the truncation objection
above says it will be cut every night and logged as policy each time. The two
answers meet there.

Held against it, and not by anyone who has run this: four strategies on one
installation are four behaviours to explain, and "who goes first" already has an
answer that balances itself, since watering the driest zone stops it being the
driest. The risk is a dropdown answering a question the model settles on its own.

A second consideration, on how much contention there is to police. Running zones
concurrently is the obvious way to shrink it, and `scheduler.md` §8 has already
stated the condition: the policy says *may I*, the hydraulics say *can I*, and
admission requires the flow of the active runs plus the candidate to fit within
the supply. Where the supply has that headroom, concurrency does shorten the
night and most of these strategies stop mattering. Where it does not, concurrency
creates no water: two zones sharing one supply each take about twice as long for
the same total, and below the pressure the emitters need it is worse than a wash,
because the water is not merely slower but badly distributed. Which case an
installation is in is not something the scheduler can assume; it is what the
measured flow rates are for.

One form of it is free, and is already written down: during a soak the pipe is
released, so serial operation plus cycle and soak interleaves without any
hydraulic headroom at all (`scheduler.md` §11). That is the concurrency available
to every installation, and it compresses exactly the runs whose wall clock is
inflated most.

A third consideration, which strengthens the case for concurrency where the
emitters allow it: **a meter read while zones are running turns the admission
condition from a prediction into an observation.** Today the third condition
compares the candidate's declared or historical rate against a supply figure
nobody measured. A live reading does better than check that arithmetic - it can
discover headroom no declared number knows about, because admitting a second zone
and watching whether total flow rises proportionally or plateaus is a direct
answer to "can I", asked of the pipe instead of of the configuration.

Two limits keep it from being the whole answer, and both are already established
elsewhere in this project.

**A meter measures volume, not pressure.** Flow can stay plausible while pressure
has sagged below what an emitter needs, and at that point a sprinkler zone is not
slower, it is watering the wrong shape. Drip tolerates the sag and pressure
compensating drip barely notices it; spray heads do not. So a flow reading can
authorise concurrency for some zones and cannot certify it for others, which
makes emitter type part of the question rather than a detail of the zone.

**The reading may be too slow to admit on.** `delivery-contract.md` is built on a
measured fact: a meter's reporting cadence is a property of the device, and the
one in the field publishes on a clock rather than per litre - a 90 s verification
window against a meter publishing every 300 s is what broke the delivery gate.
An admission decision that waits for a meter to confirm headroom inherits that
cadence, and several minutes per zone is not a decision, it is a delay. The same
rule applies as everywhere else here: a flow reading should **qualify** the
admission and never silently authorise an overlap the supply cannot feed.

Unreconciled.

### What an upgrade owes the installations that already exist

Nobody has asked for this, which is why it is written down: whatever the
scheduler becomes, an installation that upgrades and touches none of the new
settings has to behave exactly as it did before. That is a requirement on the
design, not a courtesy, and it is cheap to meet only if it is stated before the
design rather than discovered after it.

What exists today is three irrigation modes on a zone - `manual`, `reactive` and
`scheduled` - and `scheduled` carries a per-zone time of its own
(`irrigation_time`, read in `controller.py`). **Both stay properties of the zone,
and the scheduler acquires them as inputs rather than taking them over.** That is
the same relationship already established for the cycle-and-soak rule in
`scheduler.md` §11, where the scheduler does not own the rule, it interposes it:
infiltration belongs to that patch of ground, and when a zone may water belongs
to the zone too. Saying it this way settles the migration question rather than
merely answering it - a property that never changes owner has nothing to migrate.

The mode answers *whether* a zone waters and the window answers *when* it may, so
they coexist rather than replace each other, and a window left unset must mean no
constraint at all.

**The delivery pattern is a third such property, and it is the easy one.** A zone
already holds a `CycleSoakRule` with two timings, the longest segment and the
soak between segments, and `Zone.cycle_soak` is where they live. Note what the
model does *not* have: there is no once-off-versus-cycle-and-soak mode. Once-off
is both timings being unset, which is also the default, so the two states cannot
disagree with each other the way a mode and its timings can. The segment count is
not stored either - `scheduler.md` §11 derives it from the volume, because a
fourth number could contradict the other three.

It is the easy one because **nothing has been saved yet**: the rule has no field
in the configuration flow and no caller outside the model, so no installation can
have set it. There is nothing to migrate, and the requirement at the top of this
section is met by construction as long as unset keeps meaning one uninterrupted
run. Worth saying plainly all the same, because a rule that is written and
unreachable reads exactly like a rule that is in use.

So for this one the word is acquisition, not migration, and the distinction
carries work: acquiring a property nobody can set leaves the scheduler consulting
a rule that is unset on every installation, which is today's situation and is
inert. Whoever wires the scheduler owes the two timings a way in at the same
time, or the pattern ships switched off for everyone and nobody can tell.

**Decided: an installation that upgrades is one-shot.** Which costs nothing to
implement, because that is what absence already means - there is no migration
step to write, no configuration entry to version, and no way for it to go wrong
halfway through. The obligation it does create is on the form rather than on the
data: when the two timings appear in the configuration flow, an existing zone
must show as one-shot *selected*, not as two blank boxes. The state is the same
either way; the difference is whether somebody opening the options page can tell
what their zone is doing. A blank field says nothing, and this project has been
caught by that before.

The one collision is `scheduled`, because a zone's fixed hour and a site's window
are both a "when", declared at different levels. That case already has a position
taken above: an hour outside every window is not suppressed, the site wins, the
run is shifted to the first admissible time and the zone is warned with the
effective time named. So the field stays where it is and no setting is migrated.

**The risk is not a field that moves, it is a field that quietly means something
else.** Today a scheduled zone waters at its hour; if the window logic were to
start gating scheduled zones by deficit or by a projection of it, a setting
somebody saved months ago would change behaviour without anyone editing it, and
the release notes would have nothing to say because no field changed. Whatever is
decided, it has to be decided *knowingly* for `scheduled`, and said out loud.

## What this document does not do

It does not propose a design, name an object, or say which of the requests above
survive contact with each other. Several of them pull in opposite directions.
Serialising zones makes "finish before sunrise" harder, because a queue has to
start earlier for the same finish. A well gate and a master pump are both
preconditions on a run but at different scopes. An interruption for rain and a
skip for rain read alike and are not the same mechanism.

The positions above are the exception, and they are marked as one: they were
taken before the people they affect had seen them, which is why they are put up
for objection rather than presented as closed.

Those are questions for the people who asked, and they are put to them in
**[discussion #239](https://github.com/never-dry/NeverDry/discussions/239)**,
which stays open. Anything decided there comes back here as an edit, with the
reasoning, so this file stops being a collection and becomes a design only when
somebody has actually agreed to it.
