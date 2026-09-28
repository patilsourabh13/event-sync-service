"""Stage 2 -- intra-source de-duplication.

This runs *before* cross-source matching for a specific reason: the calendar
feed contains two records for the same Pinnacle meeting (CAL-A5 / CAL-A6). If
we skipped this stage, the single CRM record for that meeting would compete for
two calendar partners and one of them would be stranded as a phantom
"calendar-only" meeting.

The rule has to thread a needle. CAL-A3 and CAL-A18 are *also* near-identical --
same title, same attendees, same created_at -- but they are two occurrences of a
recurring series, not a duplicate. Requiring the same calendar day separates the
two cases cleanly, and the recurrence guard documents why that is safe.
"""

from __future__ import annotations

from ..config import (
    DEDUPE_MAX_MINUTES_APART,
    DEDUPE_MIN_PARTICIPANT_OVERLAP,
    DEDUPE_MIN_TITLE_SIMILARITY,
)
from ..models import DuplicatePair, NormalizedEvent, SourceSystem
from .text import normalize_person_name, similarity


def _participant_keys(event: NormalizedEvent) -> set[str]:
    keys = set()
    for participant in event.participants:
        key = normalize_person_name(participant.name) if participant.name else ""
        if key:
            keys.add(key)
    return keys


def _participant_overlap(a: NormalizedEvent, b: NormalizedEvent) -> float | None:
    keys_a, keys_b = _participant_keys(a), _participant_keys(b)
    if not keys_a or not keys_b:
        return None
    return len(keys_a & keys_b) / min(len(keys_a), len(keys_b))


def _minutes_apart(a: NormalizedEvent, b: NormalizedEvent) -> float:
    return abs((a.start - b.start).total_seconds()) / 60.0


def _describe_differences(kept: NormalizedEvent, dropped: NormalizedEvent) -> list[str]:
    differences = []
    if kept.start != dropped.start:
        differences.append(
            "start time {} vs {}".format(
                dropped.start.strftime("%H:%M"), kept.start.strftime("%H:%M")
            )
        )
    if (kept.location or "") != (dropped.location or ""):
        differences.append(
            "location {!r} vs {!r}".format(dropped.location, kept.location)
        )
    if kept.title != dropped.title:
        differences.append("title {!r} vs {!r}".format(dropped.title, kept.title))

    kept_people = _participant_keys(kept)
    dropped_people = _participant_keys(dropped)
    extra = kept_people - dropped_people
    missing = dropped_people - kept_people
    if extra:
        differences.append("kept record adds " + ", ".join(sorted(extra)))
    if missing:
        differences.append("dropped record had " + ", ".join(sorted(missing)))
    return differences


def _is_duplicate(a: NormalizedEvent, b: NormalizedEvent) -> tuple[bool, str]:
    """Decide whether two records from the same source describe one meeting."""
    if a.source is not b.source:
        return False, ""

    # Hard gate: same calendar day. This is what keeps the two "Weekly Team
    # Sync" occurrences (CAL-A3 on 03-11, CAL-A18 on 03-18) apart.
    if a.start.date() != b.start.date():
        return False, ""

    # Belt and braces: two occurrences of a recurring series are never
    # duplicates of each other, even if a series somehow lands twice in a day.
    if a.is_recurring and b.is_recurring and a.start != b.start:
        return False, ""

    minutes = _minutes_apart(a, b)
    if minutes > DEDUPE_MAX_MINUTES_APART:
        return False, ""

    title_score = similarity(a.title, b.title)
    overlap = _participant_overlap(a, b)

    if overlap is None:
        # No usable participants on one side -- fall back to a stricter title test.
        if title_score >= 0.75:
            return True, (
                "same source and day, {:.0f} minutes apart, and titles are {:.0%} "
                "similar (no participant lists to compare)".format(minutes, title_score)
            )
        return False, ""

    if overlap >= DEDUPE_MIN_PARTICIPANT_OVERLAP and title_score >= DEDUPE_MIN_TITLE_SIMILARITY:
        return True, (
            "same source and day, {:.0f} minutes apart, {:.0%} of the smaller "
            "attendee list overlaps, and titles are {:.0%} similar".format(
                minutes, overlap, title_score
            )
        )
    return False, ""


def _canonical_of(a: NormalizedEvent, b: NormalizedEvent) -> tuple[NormalizedEvent, NormalizedEvent]:
    """Pick which record survives.

    Most recently created wins: in both feeds created_at is when the row was
    written, so the later row reflects the more current understanding of the
    meeting. For CAL-A5/A6 that keeps A6, which is the one that includes the
    CIO the CRM note predicted would attend.
    """
    if a.created_at and b.created_at and a.created_at != b.created_at:
        return (a, b) if a.created_at > b.created_at else (b, a)
    # Fall back to the record carrying more information.
    score_a = len(a.participants) + (1 if a.location else 0) + (1 if a.notes else 0)
    score_b = len(b.participants) + (1 if b.location else 0) + (1 if b.notes else 0)
    if score_a != score_b:
        return (a, b) if score_a > score_b else (b, a)
    return (a, b) if a.record_id <= b.record_id else (b, a)


def deduplicate(
    events: list[NormalizedEvent],
) -> tuple[list[NormalizedEvent], list[DuplicatePair]]:
    """Collapse duplicate records within each source.

    Returns the surviving events (duplicates removed, survivors annotated with
    the ids they absorbed) plus a report of every collapse for the UI.
    """
    survivors: list[NormalizedEvent] = []
    duplicates: list[DuplicatePair] = []
    absorbed: set[str] = set()

    for source in (SourceSystem.CRM, SourceSystem.CALENDAR):
        pool = [e for e in events if e.source is source]
        # Deterministic order so the same input always produces the same output.
        pool.sort(key=lambda e: (e.start, e.record_id))

        for index, event in enumerate(pool):
            if event.record_id in absorbed:
                continue
            for other in pool[index + 1:]:
                if other.record_id in absorbed:
                    continue
                is_dup, reason = _is_duplicate(event, other)
                if not is_dup:
                    continue

                kept, dropped = _canonical_of(event, other)
                absorbed.add(dropped.record_id)
                kept.absorbed_record_ids.append(dropped.record_id)
                dropped.duplicate_of = kept.record_id
                duplicates.append(DuplicatePair(
                    source=source,
                    kept_record_id=kept.record_id,
                    duplicate_record_id=dropped.record_id,
                    reason=reason,
                    differences=_describe_differences(kept, dropped),
                ))
                if dropped.record_id == event.record_id:
                    # The record we were iterating from lost; stop extending it.
                    break

    for event in events:
        if event.record_id not in absorbed:
            survivors.append(event)

    return survivors, duplicates
