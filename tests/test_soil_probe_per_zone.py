"""The soil probe belongs to a zone, and moving it there must cost nobody anything.

A probe measures one patch of soil, with one kind of planting above it and its
own watering history. Declared once for the whole installation it drove every
zone, which is not a shortcut but a wrong answer: the reading is not
transferable to a zone watered independently.

The risk in fixing it is not the model — that part is arithmetic — it is the
users who already have one. Two of them reported the bugs that led here. So what
these tests hold is mostly about *them*: what happens on upgrade, what happens
while the question is unanswered, and what is never decided on their behalf.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from never_dry.const import (
    CONF_RAIN_SENSOR,
    CONF_TEMP_SENSOR,
    CONF_VWC_SENSOR,
    CONF_ZONE_AREA,
    CONF_ZONE_FIELD_CAPACITY,
    CONF_ZONE_NAME,
    CONF_ZONE_ROOT_DEPTH,
    CONF_ZONE_VWC_SENSOR,
    CONF_ZONES,
    CONFIG_VERSION,
)
from never_dry.sensor import DrynessIndexSensor, IrrigationZoneSensor

HUB = {CONF_TEMP_SENSOR: "sensor.t", CONF_RAIN_SENSOR: "sensor.r"}


def _zone(hass, dryness, **cfg):
    return IrrigationZoneSensor(hass, {CONF_ZONE_NAME: "Orto", CONF_ZONE_AREA: 20.0, **cfg}, dryness)


#: A zone whose probe has been given the two numbers it is read with.
DRIVEN = {
    CONF_ZONE_VWC_SENSOR: "sensor.orto_soil",
    CONF_ZONE_ROOT_DEPTH: 0.30,
    CONF_ZONE_FIELD_CAPACITY: 0.30,
}


def _reading(value: str):
    """A probe state-change event carrying ``value``."""
    event = MagicMock()
    event.data = {"new_state": MagicMock(state=value)}
    return event


class TestAZoneWithItsOwnProbe:
    """The probe is data, not the deficit — and that distinction was decided the hard way.

    Implementing it the other way round was rejected on field evidence: two zones
    on the same soil sit at systematically different moisture because the
    irrigation is unbalanced and one has far more shade on the ground. Both are
    circumstances of a spot, and a deficit that followed the reading would feed a
    plumbing imbalance back into the model as if it were information about the
    soil's need.
    """

    def test_the_reading_is_published(self, hass_mock):
        hub = DrynessIndexSensor(hass_mock, dict(HUB))
        zone = _zone(hass_mock, hub, **{CONF_ZONE_VWC_SENSOR: "sensor.orto_soil"})

        event = MagicMock()
        event.data = {"new_state": MagicMock(state="18.0")}  # 18 %, below field capacity
        zone._on_own_probe(event)

        attrs = zone.extra_state_attributes
        assert attrs["probe_water_content"] == pytest.approx(0.18)
        # Published beside it: the gap between this and the model's deficit after
        # an irrigation is what reveals a delivery that moved no water.
        assert attrs["probe_implied_deficit_mm"] == pytest.approx(36.0)

    def test_the_reading_does_not_touch_the_deficit_until_the_zone_says_how_to_read_it(self, hass_mock):
        """The old rule, now circumstantiated rather than deleted.

        It was never "a probe must not drive the deficit". It was "a probe must
        not drive it *on its own*, with nobody having said what to read it
        with": a reading is a fraction, and the millimetres only exist once a
        root depth and a field capacity have been declared for this patch of
        soil. Without them nothing has changed, and that is what this holds.
        """
        hub = DrynessIndexSensor(hass_mock, dict(HUB))
        zone = _zone(hass_mock, hub, **{CONF_ZONE_VWC_SENSOR: "sensor.orto_soil"})
        zone._zone_deficit = 4.0

        zone._on_own_probe(_reading("18.0"))

        assert zone._probe_drives is False
        assert zone._zone_deficit == 4.0
        assert zone.deficit_source == "site_model"

    def test_the_zone_keeps_integrating_the_model(self, hass_mock):
        """A probe adds a measurement; it does not switch the model off.

        This is also the failure mode that decided it: a probe that dies would
        otherwise freeze the deficit and stop the watering, in silence.
        """
        hub = DrynessIndexSensor(hass_mock, dict(HUB))
        zone = _zone(hass_mock, hub, **{CONF_ZONE_VWC_SENSOR: "sensor.orto_soil"})
        zone._zone_deficit = 1.0

        zone._on_et_update(1.0, 0.3, 0.0)

        assert zone._zone_deficit > 1.0

    def test_a_zone_without_one_is_untouched(self, hass_mock):
        """The change must be invisible to every zone that has no probe."""
        hub = DrynessIndexSensor(hass_mock, dict(HUB))
        zone = _zone(hass_mock, hub)
        zone._zone_deficit = 1.0

        zone._on_et_update(1.0, 0.2, 0.0)

        assert zone._zone_deficit > 1.0

    def test_a_percentage_reading_is_converted_not_believed(self, hass_mock):
        """Consumer probes report 45, not 0.45 — read raw, every reading looks saturated."""
        hub = DrynessIndexSensor(hass_mock, dict(HUB))
        zone = _zone(hass_mock, hub, **{CONF_ZONE_VWC_SENSOR: "sensor.orto_soil"})

        event = MagicMock()
        event.data = {"new_state": MagicMock(state="45")}
        zone._on_own_probe(event)

        assert zone.extra_state_attributes["probe_water_content"] == pytest.approx(0.45)

    def test_an_unreadable_probe_publishes_nothing(self, hass_mock):
        """A missing reading is not a dry soil, and not a wet one either."""
        hub = DrynessIndexSensor(hass_mock, dict(HUB))
        zone = _zone(hass_mock, hub, **{CONF_ZONE_VWC_SENSOR: "sensor.orto_soil"})

        event = MagicMock()
        event.data = {"new_state": MagicMock(state="unavailable")}
        zone._on_own_probe(event)

        assert "probe_water_content" not in zone.extra_state_attributes


class TestTheUpgrade:
    """What happens to the people who already have one."""

    def _entry(self, version, data):
        entry = MagicMock()
        entry.version = version
        entry.data = data
        return entry

    @pytest.mark.asyncio
    async def test_one_zone_needs_no_question(self, hass_mock):
        """With a single zone the probe is in it. Asking would be theatre."""
        from never_dry import async_migrate_entry

        entry = self._entry(
            3,
            {
                **HUB,
                CONF_VWC_SENSOR: "sensor.soil",
                CONF_ZONES: [{CONF_ZONE_NAME: "Orto", CONF_ZONE_AREA: 20.0}],
            },
        )
        captured = {}
        hass_mock.config_entries.async_update_entry = lambda e, **kw: captured.update(kw)

        assert await async_migrate_entry(hass_mock, entry) is True

        zones = captured["data"][CONF_ZONES]
        assert zones[0][CONF_ZONE_VWC_SENSOR] == "sensor.soil"
        assert CONF_VWC_SENSOR not in captured["data"]
        assert captured["version"] == CONFIG_VERSION

    @pytest.mark.asyncio
    async def test_several_zones_are_left_exactly_as_they_were(self, hass_mock):
        """Only the user knows where it is buried, so nothing is guessed.

        And nothing is deleted: removing the binding would degrade those zones
        to an estimate in silence and throw away an entity they had supplied.
        The installation keeps behaving as before while the question waits.
        """
        from never_dry import async_migrate_entry

        entry = self._entry(
            3,
            {
                **HUB,
                CONF_VWC_SENSOR: "sensor.soil",
                CONF_ZONES: [
                    {CONF_ZONE_NAME: "Orto", CONF_ZONE_AREA: 20.0},
                    {CONF_ZONE_NAME: "Prato", CONF_ZONE_AREA: 40.0},
                ],
            },
        )
        captured = {}
        hass_mock.config_entries.async_update_entry = lambda e, **kw: captured.update(kw)

        assert await async_migrate_entry(hass_mock, entry) is True

        assert captured["data"][CONF_VWC_SENSOR] == "sensor.soil"
        assert all(CONF_ZONE_VWC_SENSOR not in z for z in captured["data"][CONF_ZONES])

    @pytest.mark.asyncio
    async def test_an_installation_without_a_probe_is_not_disturbed(self, hass_mock):
        from never_dry import async_migrate_entry

        entry = self._entry(3, {**HUB, CONF_ZONES: [{CONF_ZONE_NAME: "Orto", CONF_ZONE_AREA: 20.0}]})
        captured = {}
        hass_mock.config_entries.async_update_entry = lambda e, **kw: captured.update(kw)

        assert await async_migrate_entry(hass_mock, entry) is True
        assert CONF_VWC_SENSOR not in captured["data"]


class TestTheQuestionAsked:
    """The repair issue: raised when it is needed, gone when it is not."""

    def _hass_with_registry(self, monkeypatch):
        import sys
        from types import SimpleNamespace

        created, deleted = [], []
        fake = SimpleNamespace(
            async_create_issue=lambda hass, domain, issue_id, **kw: created.append((issue_id, kw)),
            async_delete_issue=lambda hass, domain, issue_id: deleted.append(issue_id),
            IssueSeverity=SimpleNamespace(WARNING="warning"),
        )
        monkeypatch.setattr(sys.modules["homeassistant.helpers"], "issue_registry", fake, raising=False)
        monkeypatch.setitem(sys.modules, "homeassistant.helpers.issue_registry", fake)
        return created, deleted

    def _entry(self, data, entry_id="e1"):
        entry = MagicMock()
        entry.data = data
        entry.entry_id = entry_id
        return entry

    def test_it_is_raised_when_several_zones_share_one_probe(self, hass_mock, monkeypatch):
        from never_dry.repairs import async_check_soil_probe

        created, _ = self._hass_with_registry(monkeypatch)
        entry = self._entry(
            {
                CONF_VWC_SENSOR: "sensor.soil",
                CONF_ZONES: [{CONF_ZONE_NAME: "Orto"}, {CONF_ZONE_NAME: "Prato"}],
            }
        )

        async_check_soil_probe(hass_mock, entry)

        assert created and created[0][1]["is_fixable"] is True
        assert created[0][1]["translation_placeholders"]["probe"] == "sensor.soil"

    def test_it_is_not_raised_when_there_is_nothing_to_ask(self, hass_mock, monkeypatch):
        """One zone was migrated automatically; no probe means no question."""
        from never_dry.repairs import async_check_soil_probe

        created, deleted = self._hass_with_registry(monkeypatch)
        async_check_soil_probe(hass_mock, self._entry({CONF_ZONES: [{CONF_ZONE_NAME: "Orto"}]}))

        assert not created
        assert deleted  # and any stale one is cleared

    def test_answering_by_editing_the_zone_clears_it(self, hass_mock, monkeypatch):
        """The user may answer the question without ever opening the repair.

        Checked at every setup for this reason: someone who moves the probe into
        a zone by hand has answered it, and should not be asked again.
        """
        from never_dry.repairs import async_check_soil_probe

        created, deleted = self._hass_with_registry(monkeypatch)
        entry = self._entry(
            {
                CONF_ZONES: [
                    {CONF_ZONE_NAME: "Orto", CONF_ZONE_VWC_SENSOR: "sensor.soil"},
                    {CONF_ZONE_NAME: "Prato"},
                ]
            }
        )

        async_check_soil_probe(hass_mock, entry)

        assert not created
        assert deleted


class TestTheProbeDrivesOnceItIsToldWhatToReadItWith:
    """The two numbers are the switch, and they are also the responsibility.

    A fraction becomes millimetres by being multiplied by a root depth, and no
    root depth is right for every planting: the same 18 % reading is 18 mm under
    a lawn and 72 mm under a hedge. NeverDry cannot know which, the gardener can,
    and the act of typing the pair is the act of saying so. What the integration
    owes in return is to never pretend the number was measured when half of it
    was declared -- which is why the scaling is published beside the result.
    """

    def test_both_numbers_present_hands_the_deficit_to_the_soil(self, hass_mock):
        hub = DrynessIndexSensor(hass_mock, dict(HUB))
        zone = _zone(hass_mock, hub, **DRIVEN)
        zone._zone_deficit = 4.0

        zone._on_own_probe(_reading("18.0"))

        # (0.30 - 0.18) * 0.30 m * 1000
        assert zone._zone_deficit == pytest.approx(36.0)
        assert zone.deficit_source == "zone_probe"

    def test_the_zone_numbers_are_used_and_not_the_site_ones(self, hass_mock):
        """The whole point of moving them onto the zone."""
        hub = DrynessIndexSensor(hass_mock, dict(HUB))
        zone = _zone(hass_mock, hub, **{**DRIVEN, CONF_ZONE_ROOT_DEPTH: 0.60})

        zone._on_own_probe(_reading("18.0"))

        assert zone._zone_deficit == pytest.approx(72.0)

    def test_what_scaled_the_reading_is_published_beside_it(self, hass_mock):
        """A declaration wearing the clothes of a measurement has to say so."""
        hub = DrynessIndexSensor(hass_mock, dict(HUB))
        zone = _zone(hass_mock, hub, **DRIVEN)
        zone._on_own_probe(_reading("18.0"))

        attrs = zone.extra_state_attributes

        assert attrs["deficit_source"] == "zone_probe"
        assert attrs["probe_root_depth_m"] == 0.30
        assert attrs["probe_field_capacity"] == 0.30

    def test_a_zone_with_no_probe_says_the_model_answers_for_it(self, hass_mock):
        hub = DrynessIndexSensor(hass_mock, dict(HUB))
        assert _zone(hass_mock, hub).extra_state_attributes["deficit_source"] == "site_model"


class TestTheModelKeepsRunningUnderneath:
    """The reserve is not rebuilt after the failure; it was never switched off.

    A probe fails when its battery does, which is to say without warning and
    usually in the dry half of the year. A reserve that started integrating at
    that moment would start from zero with the garden already thirsty, so the
    estimate advances the whole time, published or not.
    """

    def test_the_estimate_advances_while_the_probe_is_driving(self, hass_mock):
        hub = DrynessIndexSensor(hass_mock, dict(HUB))
        zone = _zone(hass_mock, hub, **DRIVEN)
        zone._zone_deficit = 1.0
        zone._on_own_probe(_reading("18.0"))

        zone._on_et_update(1.0, 0.3, 0.0)

        assert zone._et_deficit > 1.0, "the reserve stopped advancing"
        assert zone._zone_deficit == pytest.approx(36.0), "the published number left the soil"

    def test_the_estimate_is_not_seeded_from_the_soil_each_tick(self, hass_mock):
        """The trap in sharing one accessor: the integration would restart from
        the probe every hour and the reserve would only ever be one tick old."""
        hub = DrynessIndexSensor(hass_mock, dict(HUB))
        zone = _zone(hass_mock, hub, **DRIVEN)
        zone._zone_deficit = 1.0
        zone._on_own_probe(_reading("18.0"))

        zone._on_et_update(1.0, 0.3, 0.0)
        first = zone._et_deficit
        zone._on_et_update(1.0, 0.3, 0.0)

        assert zone._et_deficit > first
        assert first < 36.0, "the reserve was seeded from the probe instead of integrating"


class TestAProbeThatStopsSpeaking:
    """The failure the freshness check exists for, and the only one that is silent.

    A probe whose battery dies keeps its last value on display for as long as
    anyone cares to look. Believed, it would report damp soil from June until
    September and the zone would never be watered again -- a fault that presents
    as nothing at all.
    """

    def _driven_zone(self, hass_mock):
        hub = DrynessIndexSensor(hass_mock, dict(HUB))
        zone = _zone(hass_mock, hub, **DRIVEN)
        zone._zone_deficit = 4.0
        zone._on_own_probe(_reading("18.0"))
        return zone

    def test_quiet_for_longer_than_it_has_ever_been_falls_back(self, hass_mock):
        zone = self._driven_zone(hass_mock)
        zone._probe_intervals.extend([300.0, 310.0, 305.0])
        zone._probe_last_seen = datetime.now(UTC) - timedelta(minutes=20)

        assert zone._probe_is_fresh() is False
        assert zone.deficit_source == "site_model"
        assert zone._zone_deficit == 4.0

    def test_quiet_within_its_own_cadence_is_believed(self, hass_mock):
        """The bar is this probe's habit, never a constant: one sensor speaks
        every thirty seconds and another twice a day."""
        zone = self._driven_zone(hass_mock)
        zone._probe_intervals.extend([300.0, 310.0, 305.0])
        zone._probe_last_seen = datetime.now(UTC) - timedelta(minutes=2)

        assert zone._probe_is_fresh() is True
        assert zone._zone_deficit == pytest.approx(36.0)

    def test_with_no_cadence_yet_the_reading_still_stands(self, hass_mock):
        """No samples is not evidence of silence, and refusing a good reading
        would send the zone onto the estimate for no reason at all."""
        zone = self._driven_zone(hass_mock)

        assert not zone._probe_intervals
        assert zone._probe_is_fresh() is True

    def test_the_backstop_catches_a_probe_that_never_established_one(self, hass_mock):
        """The case its own bar cannot see: dead before it ever had a habit."""
        zone = self._driven_zone(hass_mock)
        zone._probe_last_seen = datetime.now(UTC) - timedelta(hours=48)

        assert not zone._probe_intervals
        assert zone._probe_is_fresh() is False
        assert zone._zone_deficit == 4.0

    def test_the_fall_is_said_out_loud_once(self, hass_mock, caplog):
        zone = self._driven_zone(hass_mock)
        zone._probe_last_seen = datetime.now(UTC) - timedelta(hours=48)

        zone._on_et_update(1.0, 0.3, 0.0)
        zone._on_et_update(1.0, 0.3, 0.0)

        assert sum("stopped reporting" in r.message for r in caplog.records) == 1

    def test_speaking_again_is_enough_to_be_believed_again(self, hass_mock):
        """No mode to leave: an afternoon of quiet costs an afternoon."""
        zone = self._driven_zone(hass_mock)
        zone._probe_last_seen = datetime.now(UTC) - timedelta(hours=48)
        assert zone.deficit_source == "site_model"

        zone._on_own_probe(_reading("18.0"))

        assert zone.deficit_source == "zone_probe"


class TestWhatTheFormSaysAboutTheProbesRole:
    """The defect behind the report was a belief, not a number.

    Binding a probe to a zone reads as "this zone now waters by what the soil
    says". Nothing contradicted it, and the deficit went on coming from the
    weather.
    """

    def test_a_probe_without_its_numbers_is_told_it_will_not_drive(self):
        from never_dry.config_flow import _probe_role_warnings

        warnings = _probe_role_warnings({CONF_ZONE_VWC_SENSOR: "sensor.soil"})

        assert len(warnings) == 1
        assert "will not set" in warnings[0]
        assert "root depth and field capacity" in warnings[0]

    def test_half_the_pair_names_the_half_that_is_missing(self):
        from never_dry.config_flow import _probe_role_warnings

        warnings = _probe_role_warnings({CONF_ZONE_VWC_SENSOR: "sensor.soil", CONF_ZONE_ROOT_DEPTH: 0.3})

        assert "field capacity is missing" in warnings[0]

    def test_the_complete_pair_says_nothing_because_the_choice_was_made(self):
        from never_dry.config_flow import _probe_role_warnings

        assert _probe_role_warnings(dict(DRIVEN)) == []

    def test_numbers_with_no_probe_to_read_are_flagged_as_unused(self):
        """The same shape as the ignored-override warnings: a value nobody reads."""
        from never_dry.config_flow import _probe_role_warnings

        warnings = _probe_role_warnings({CONF_ZONE_ROOT_DEPTH: 0.3, CONF_ZONE_FIELD_CAPACITY: 0.25})

        assert "will not be used" in warnings[0]

    def test_a_zone_with_neither_is_not_lectured(self):
        from never_dry.config_flow import _probe_role_warnings

        assert _probe_role_warnings({CONF_ZONE_NAME: "Orto"}) == []


def test_root_depth_is_a_length_and_crosses_the_unit_boundary():
    """Entered in inches on an imperial form, stored in metres like everything else."""
    from never_dry.unit_convert import zone_input_to_metric

    metric = zone_input_to_metric({CONF_ZONE_ROOT_DEPTH: 12.0}, True)

    assert metric[CONF_ZONE_ROOT_DEPTH] == pytest.approx(0.3048)


def test_field_capacity_is_a_fraction_and_does_not():
    from never_dry.unit_convert import zone_input_to_metric

    assert zone_input_to_metric({CONF_ZONE_FIELD_CAPACITY: 0.25}, True)[CONF_ZONE_FIELD_CAPACITY] == 0.25
