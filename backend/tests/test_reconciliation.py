"""Tests for the reconciliation pipeline.

These are not here for coverage. Each one pins a specific judgment call about
the supplied dataset, so that if someone changes a threshold or a normalization
rule, the test that fails tells them which real-world case they just broke.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import Coverage, MeetingStatus, Modality, Severity, SourceSystem
from app.pipeline.dedupe import deduplicate
from app.pipeline.ingest import ingest_all, parse_date_loose, parse_timestamp_loose, repair_email
from app.pipeline.matching import match_events
from app.sync import run_sync


@pytest.fixture(scope="module")
def result():
    return run_sync()


@pytest.fixture(scope="module")
def events():
    return ingest_all()[0]


def find_event(events, record_id):
    return next(e for e in events if e.record_id == record_id)


def find_meeting(result, record_id):
    for meeting in result.meetings:
        if any(s.record_id == record_id for s in meeting.sources):
            return meeting
    raise AssertionError("no unified meeting contains " + record_id)


# --------------------------------------------------------------------------
# Ingest: malformed and inconsistent fields
# --------------------------------------------------------------------------
class TestMalformedInput:
    def test_crm_1008_slash_date_is_salvaged(self, events):
        """CRM-1008's meeting_date is "03-15/2025", not ISO."""
        event = find_event(events, "CRM-1008")
        assert event.start.date() == date(2025, 3, 15)
        assert any(w.code == "date_salvaged" for w in event.warnings)

    def test_salvage_is_reported_not_silent(self, events):
        warning = next(w for w in find_event(events, "CRM-1008").warnings
                       if w.code == "date_salvaged")
        assert warning.original_value == "03-15/2025"
        assert warning.repaired_value == "2025-03-15"

    @pytest.mark.parametrize("raw,expected", [
        ("2025-03-10", date(2025, 3, 10)),
        ("03-15/2025", date(2025, 3, 15)),
        ("2025/03/15", date(2025, 3, 15)),
        ("not a date", None),
        ("", None),
        (None, None),
    ])
    def test_date_parser_cases(self, raw, expected):
        assert parse_date_loose(raw)[0] == expected

    def test_cal_a11_missing_seconds_does_not_crash(self, events):
        """CAL-A11's end_time is "2025-03-14T20:00" while siblings carry seconds."""
        event = find_event(events, "CAL-A11")
        assert event.end is not None
        assert any(w.code == "timestamp_format" for w in event.warnings)

    def test_cal_a11_empty_fields_are_tolerated(self, events):
        event = find_event(events, "CAL-A11")
        assert event.location is None
        codes = {w.code for w in event.warnings}
        assert {"no_attendees", "missing_location", "missing_description"} <= codes

    def test_bracketed_email_is_repaired(self):
        email, note = repair_email("raj.patel[at]atlasvc.com")
        assert email == "raj.patel@atlasvc.com"
        assert note

    def test_non_email_attendee_is_kept_but_flagged(self, events):
        """CAL-A20 lists "external-guests", which is not a person."""
        event = find_event(events, "CAL-A20")
        assert any(p.raw == "external-guests" and p.email is None for p in event.participants)
        assert any(w.code == "unparseable_attendee" for w in event.warnings)

    def test_nothing_is_dropped_silently(self, result):
        """Every input record is either reconciled or explicitly quarantined."""
        accounted = sum(len(m.sources) for m in result.meetings)
        accounted += result.stats.intra_source_duplicates
        accounted += result.stats.quarantined
        assert accounted == result.stats.total_records_read

    def test_no_record_is_quarantined_from_the_sample_data(self, result):
        """Every defect in the sample set is recoverable; none should be rejected."""
        assert result.quarantined == []


# --------------------------------------------------------------------------
# Ingest: timezone handling
# --------------------------------------------------------------------------
class TestTimezones:
    def test_cal_a4_utc_stamp_is_converted_to_org_time(self, events):
        """CAL-A4 is the only record with an explicit Z; 19:00Z is 15:00 EDT."""
        event = find_event(events, "CAL-A4")
        assert event.start.hour == 15
        assert event.start.date() == date(2025, 3, 13)
        assert any(w.code == "timezone_normalized" for w in event.warnings)

    def test_naive_stamps_are_treated_as_org_local(self, events):
        """CAL-A1 says 14:00 with no zone and must stay 14:00."""
        assert find_event(events, "CAL-A1").start.hour == 14

    def test_naive_and_zoned_records_land_on_the_same_day(self, events):
        crm = find_event(events, "CRM-1004")
        cal = find_event(events, "CAL-A4")
        assert crm.start.date() == cal.start.date()

    @pytest.mark.parametrize("raw,expected_hour", [
        ("2025-03-13T19:00:00Z", 15),   # UTC -> EDT
        ("2025-03-13T14:00:00", 14),    # naive, already org-local
    ])
    def test_timestamp_parser_cases(self, raw, expected_hour):
        assert parse_timestamp_loose(raw)[0].hour == expected_hour


# --------------------------------------------------------------------------
# Intra-source de-duplication
# --------------------------------------------------------------------------
class TestDeduplication:
    def test_cal_a5_and_a6_are_one_meeting(self, events):
        """The planted intra-source duplicate: same client, 30 minutes apart."""
        survivors, duplicates = deduplicate(list(events))
        pair = next(d for d in duplicates if {d.kept_record_id, d.duplicate_record_id}
                    == {"CAL-A5", "CAL-A6"})
        assert pair.source is SourceSystem.CALENDAR
        surviving_ids = {e.record_id for e in survivors}
        assert len({"CAL-A5", "CAL-A6"} & surviving_ids) == 1

    def test_later_created_record_survives(self, events):
        """CAL-A6 was written later and adds the CIO the CRM note predicted."""
        _, duplicates = deduplicate(list(events))
        pair = next(d for d in duplicates if "CAL-A5" in
                    {d.kept_record_id, d.duplicate_record_id})
        assert pair.kept_record_id == "CAL-A6"

    def test_recurring_occurrences_are_not_duplicates(self, events):
        """CAL-A3 and CAL-A18 look identical but are a week apart.

        This is the counter-case to CAL-A5/A6 and the reason de-duplication is
        gated on the same calendar day.
        """
        survivors, _ = deduplicate(list(events))
        surviving_ids = {e.record_id for e in survivors}
        assert {"CAL-A3", "CAL-A18"} <= surviving_ids

    def test_dedupe_reports_what_differed(self, events):
        _, duplicates = deduplicate(list(events))
        pair = next(d for d in duplicates if d.duplicate_record_id == "CAL-A5")
        assert pair.differences, "a collapse must explain what it discarded"
        assert any("11:00" in d for d in pair.differences)

    def test_one_crm_record_does_not_claim_two_calendar_events(self, result):
        """Without de-duplication CRM-1005 would compete for both A5 and A6."""
        meeting = find_meeting(result, "CRM-1005")
        assert meeting.coverage is Coverage.BOTH
        calendar_ids = [s.record_id for s in meeting.sources
                        if s.source is SourceSystem.CALENDAR]
        assert len(calendar_ids) == 1


# --------------------------------------------------------------------------
# Cross-source matching
# --------------------------------------------------------------------------
class TestMatching:
    EXPECTED_PAIRS = {
        "CRM-1001": "CAL-A1",   "CRM-1002": "CAL-A2",   "CRM-1004": "CAL-A4",
        "CRM-1005": "CAL-A6",   "CRM-1006": "CAL-A7",   "CRM-1007": "CAL-A8",
        "CRM-1008": "CAL-A9",   "CRM-1009": "CAL-A10",  "CRM-1011": "CAL-A12",
        "CRM-1012": "CAL-A13",  "CRM-1013": "CAL-A14",  "CRM-1014": "CAL-A15",
        "CRM-1015": "CAL-A16",  "CRM-1016": "CAL-A17",  "CRM-1017": "CAL-A20",
        "CRM-1018": "CAL-A21",  "CRM-1019": "CAL-A22",
    }

    def test_expected_pairs(self, events):
        survivors, _ = deduplicate(list(events))
        matched, _ = match_events(survivors)
        actual = {m.crm_record_id: m.calendar_record_id for m in matched}
        assert actual == self.EXPECTED_PAIRS

    def test_matching_is_one_to_one(self, events):
        survivors, _ = deduplicate(list(events))
        matched, _ = match_events(survivors)
        crm_ids = [m.crm_record_id for m in matched]
        cal_ids = [m.calendar_record_id for m in matched]
        assert len(crm_ids) == len(set(crm_ids))
        assert len(cal_ids) == len(set(cal_ids))

    def test_unmatched_records_stay_single_source(self, result):
        crm_only = {m.sources[0].record_id for m in result.meetings
                    if m.coverage is Coverage.CRM_ONLY}
        calendar_only = {m.sources[0].record_id for m in result.meetings
                         if m.coverage is Coverage.CALENDAR_ONLY}
        assert crm_only == {"CRM-1003", "CRM-1010", "CRM-1020"}
        assert calendar_only == {"CAL-A3", "CAL-A11", "CAL-A18", "CAL-A19"}

    def test_dissimilar_titles_still_match_on_client_and_time(self, result):
        """'Annual Allocation Review' vs 'Horizon Wealth - Year-End Review'.

        Proves title similarity alone is not load-bearing.
        """
        meeting = find_meeting(result, "CRM-1011")
        assert meeting.coverage is Coverage.BOTH

    def test_two_hour_gap_still_matches_and_flags(self, result):
        """CRM-1016 at 13:00 vs CAL-A17 at 15:00: same client, same day.

        Matched deliberately -- a two-hour drift is the kind of thing a sync
        service exists to surface, not a reason to call them different meetings.
        """
        meeting = find_meeting(result, "CRM-1016")
        assert meeting.coverage is Coverage.BOTH
        conflict = next(c for c in meeting.conflicts if c.field == "start")
        assert conflict.severity is Severity.HIGH

    def test_records_on_different_days_never_match(self, events):
        survivors, _ = deduplicate(list(events))
        matched, _ = match_events(survivors)
        by_id = {e.record_id: e for e in survivors}
        for decision in matched:
            assert (by_id[decision.crm_record_id].start.date()
                    == by_id[decision.calendar_record_id].start.date())

    def test_every_match_carries_its_reasoning(self, result):
        for meeting in result.meetings:
            if meeting.coverage is Coverage.BOTH:
                assert meeting.match is not None
                assert meeting.match.signals
                assert meeting.match.score >= 0.70

    def test_single_source_meetings_explain_themselves(self, result):
        """A CRM-only meeting on a day with calendar events should say why."""
        meeting = find_meeting(result, "CRM-1003")
        assert meeting.closest_candidate is not None
        assert meeting.closest_candidate.score < 0.70


# --------------------------------------------------------------------------
# Conflict detection and resolution
# --------------------------------------------------------------------------
class TestConflicts:
    def test_zoom_versus_in_person_is_flagged(self, result):
        """The conflict named in the brief: CRM says In-Person, calendar says Zoom."""
        meeting = find_meeting(result, "CRM-1002")
        fields = {c.field for c in meeting.conflicts}
        assert "modality" in fields
        assert "location" in fields
        modality = next(c for c in meeting.conflicts if c.field == "modality")
        assert modality.severity is Severity.HIGH
        assert modality.crm_value == Modality.IN_PERSON.value
        assert modality.calendar_value == Modality.VIRTUAL.value

    def test_cancellation_is_critical_and_always_wins(self, result):
        """CRM-1009 is cancelled; CAL-A10 still says confirmed."""
        meeting = find_meeting(result, "CRM-1009")
        conflict = next(c for c in meeting.conflicts if c.field == "status")
        assert conflict.severity is Severity.CRITICAL
        assert meeting.status.value == MeetingStatus.CANCELLED.value
        assert meeting.status.chosen_from is SourceSystem.CRM

    def test_timezone_drift_surfaces_as_a_time_conflict(self, result):
        """CRM-1004 14:00 vs CAL-A4 19:00Z (15:00 local) -- a real 1h disagreement."""
        meeting = find_meeting(result, "CRM-1004")
        conflict = next(c for c in meeting.conflicts if c.field == "start")
        assert conflict.severity is Severity.MEDIUM

    def test_owner_mismatch_is_flagged(self, result):
        """CRM-1013 is owned by Sarah Chen but organized by Priya Sharma."""
        meeting = find_meeting(result, "CRM-1013")
        conflict = next(c for c in meeting.conflicts if c.field == "owner")
        assert conflict.crm_value == "Sarah Chen"
        assert conflict.calendar_value == "Priya Sharma"
        assert meeting.owner.value == "Sarah Chen"

    def test_more_specific_location_is_not_a_conflict(self, result):
        """'HQ - Conference Room B' and 'Conference Room B' are the same room."""
        meeting = find_meeting(result, "CRM-1001")
        assert not any(c.field == "location" for c in meeting.conflicts)

    def test_missing_field_is_a_gap_fill_not_a_conflict(self, result):
        """CRM-1007 has no time; CAL-A8 supplies 15:00. That is not a disagreement."""
        meeting = find_meeting(result, "CRM-1007")
        assert not any(c.field == "start" for c in meeting.conflicts)
        assert meeting.start.chosen_from is SourceSystem.CALENDAR
        assert meeting.start.value.endswith("15:00:00-04:00")

    def test_losing_value_is_never_discarded(self, result):
        """Both sides of every conflict remain available to the UI."""
        for meeting in result.meetings:
            for conflict in meeting.conflicts:
                field = getattr(meeting, conflict.field)
                assert len(field.candidates) == 2
                sources = {c.source for c in field.candidates}
                assert sources == {SourceSystem.CRM, SourceSystem.CALENDAR}

    def test_every_conflict_is_explained_in_plain_language(self, result):
        for conflict in result.conflicts:
            assert conflict.explanation
            assert conflict.label
            assert conflict.severity in set(Severity)

    def test_single_source_meetings_have_no_conflicts(self, result):
        for meeting in result.meetings:
            if meeting.coverage is not Coverage.BOTH:
                assert meeting.conflicts == []


# --------------------------------------------------------------------------
# Whole-pipeline invariants
# --------------------------------------------------------------------------
class TestPipeline:
    def test_headline_counts(self, result):
        stats = result.stats
        assert stats.total_records_read == 42
        assert stats.unified_meetings == 24
        assert stats.matched_both_sources == 17
        assert stats.crm_only == 3
        assert stats.calendar_only == 4
        assert stats.intra_source_duplicates == 1

    def test_meeting_ids_are_stable_across_runs(self):
        first = {m.id for m in run_sync().meetings}
        second = {m.id for m in run_sync().meetings}
        assert first == second

    def test_conflict_totals_agree_with_per_meeting_counts(self, result):
        assert sum(m.conflict_summary.total for m in result.meetings) == len(result.conflicts)
