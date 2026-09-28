"""Pipeline orchestration.

Ingest -> de-duplicate -> match -> merge, with the intermediate artefacts of
each stage kept rather than discarded. The API serves those artefacts directly,
which is what lets the UI explain itself instead of just asserting results.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .models import (
    ConflictSummary,
    Coverage,
    NormalizedEvent,
    SourceSystem,
    SyncResult,
    SyncStats,
    UnifiedMeeting,
)
from .pipeline.dedupe import deduplicate
from .pipeline.ingest import ingest_all
from .pipeline.matching import match_events, score_pair
from .pipeline.merge import build_meeting

_COVERAGE_ORDER = {Coverage.BOTH: 0, Coverage.CRM_ONLY: 1, Coverage.CALENDAR_ONLY: 2}


def _closest_candidate(event: NormalizedEvent, pool: list[NormalizedEvent]):
    """Best same-day partner we scored and did not accept, for explainability."""
    crm_side = event.source is SourceSystem.CRM
    best = None
    for other in pool:
        if other.source is event.source:
            continue
        if other.start.date() != event.start.date():
            continue
        decision = score_pair(event, other) if crm_side else score_pair(other, event)
        if best is None or decision.score > best.score:
            best = decision
    return best


def run_sync(
    crm_path: Path | None = None,
    calendar_path: Path | None = None,
) -> SyncResult:
    events, warnings, quarantined, counts = ingest_all(crm_path, calendar_path)
    survivors, duplicates = deduplicate(events)
    matched, review_queue = match_events(survivors)

    by_id = {e.record_id: e for e in survivors}
    meetings: list[UnifiedMeeting] = []
    claimed: set[str] = set()

    for decision in matched:
        crm = by_id[decision.crm_record_id]
        cal = by_id[decision.calendar_record_id]
        claimed.update({crm.record_id, cal.record_id})
        meetings.append(build_meeting(crm, cal, decision))

    for event in survivors:
        if event.record_id in claimed:
            continue
        crm = event if event.source is SourceSystem.CRM else None
        cal = event if event.source is SourceSystem.CALENDAR else None
        meeting = build_meeting(crm, cal, None)
        meeting.closest_candidate = _closest_candidate(event, survivors)
        meetings.append(meeting)

    meetings.sort(key=lambda m: (m.start.value or "", _COVERAGE_ORDER[m.coverage]))

    conflicts = [c for m in meetings for c in m.conflicts]

    severity_totals = ConflictSummary()
    for conflict in conflicts:
        key = conflict.severity.value
        setattr(severity_totals, key, getattr(severity_totals, key) + 1)
    severity_totals.total = len(conflicts)

    records_with_warnings = len({(w.source, w.record_id) for w in warnings})

    stats = SyncStats(
        crm_records_read=counts["crm"],
        calendar_records_read=counts["calendar"],
        total_records_read=counts["crm"] + counts["calendar"],
        quarantined=len(quarantined),
        intra_source_duplicates=len(duplicates),
        unified_meetings=len(meetings),
        matched_both_sources=sum(1 for m in meetings if m.coverage is Coverage.BOTH),
        crm_only=sum(1 for m in meetings if m.coverage is Coverage.CRM_ONLY),
        calendar_only=sum(1 for m in meetings if m.coverage is Coverage.CALENDAR_ONLY),
        meetings_with_conflicts=sum(1 for m in meetings if m.conflicts),
        total_conflicts=len(conflicts),
        conflicts_by_severity=severity_totals,
        needs_review=len(review_queue),
        records_with_warnings=records_with_warnings,
        generated_at=datetime.now(timezone.utc),
    )

    return SyncResult(
        meetings=meetings,
        conflicts=conflicts,
        review_queue=review_queue,
        duplicates=duplicates,
        warnings=warnings,
        quarantined=quarantined,
        normalized={e.record_id: e for e in events},
        stats=stats,
    )


class SyncStore:
    """Holds the most recent sync so requests do not re-read the files.

    The data sources here are static files. A real deployment would poll the
    upstream APIs on a schedule; the seam for that is POST /api/sync.
    """

    def __init__(self) -> None:
        self._result: SyncResult | None = None

    @property
    def result(self) -> SyncResult:
        if self._result is None:
            self._result = run_sync()
        return self._result

    def refresh(self) -> SyncResult:
        self._result = run_sync()
        return self._result


store = SyncStore()
