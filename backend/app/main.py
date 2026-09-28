"""REST API for the Event Sync Service.

Endpoint design follows from the brief: as well as serving the reconciled list,
the API exposes *how* the list was produced -- conflicts, the review queue, the
de-duplication report, and the per-record data-quality warnings. Interactive
docs are generated at /docs.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import (
    AUTO_MATCH_THRESHOLD,
    ORG_TIMEZONE,
    REVIEW_THRESHOLD,
    SIGNAL_WEIGHTS,
)
from .models import Coverage, Severity, SourceSystem
from .sync import store

app = FastAPI(
    title="Event Sync Service",
    version="1.0.0",
    description=(
        "Reconciles meeting records from a CRM feed and a calendar feed into a "
        "single list, keeping every source disagreement visible instead of "
        "silently picking a winner."
    ),
)

# The Vite dev server runs on a different port during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:4173", "http://127.0.0.1:4173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _parse_day(value: str | None, label: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, "{} must be an ISO date (YYYY-MM-DD)".format(label))


def _meeting_day(meeting) -> date | None:
    if not meeting.start.value:
        return None
    return datetime.fromisoformat(meeting.start.value).date()


def _haystack(meeting) -> str:
    parts = [
        meeting.title.value, meeting.client_name.value, meeting.client_company.value,
        meeting.owner.value, meeting.location.value, meeting.notes.value,
    ]
    parts.extend(source.record_id for source in meeting.sources)
    return " ".join(str(p) for p in parts if p).lower()


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@app.get("/api/health", tags=["meta"])
def health() -> dict[str, Any]:
    return {"status": "ok", "generated_at": store.result.stats.generated_at}


@app.get("/api/stats", tags=["meta"])
def stats():
    """Headline numbers for the sync: what went in, what came out."""
    return store.result.stats


@app.get("/api/config", tags=["meta"])
def reconciliation_config() -> dict[str, Any]:
    """The tunable policy behind the results, so the UI can show its own rules."""
    return {
        "org_timezone": ORG_TIMEZONE,
        "auto_match_threshold": AUTO_MATCH_THRESHOLD,
        "review_threshold": REVIEW_THRESHOLD,
        "signal_weights": SIGNAL_WEIGHTS,
    }


@app.get("/api/meetings", tags=["meetings"])
def list_meetings(
    coverage: Coverage | None = Query(None, description="both | crm_only | calendar_only"),
    has_conflicts: bool | None = Query(None),
    min_severity: Severity | None = Query(
        None, description="only meetings carrying a conflict at least this severe"
    ),
    status: str | None = Query(None),
    q: str | None = Query(None, description="free-text search"),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    sort: Literal["start", "conflicts"] = Query("start"),
):
    """The reconciled meeting list."""
    meetings = list(store.result.meetings)

    if coverage:
        meetings = [m for m in meetings if m.coverage is coverage]
    if has_conflicts is not None:
        meetings = [m for m in meetings if bool(m.conflicts) is has_conflicts]
    if status:
        wanted = status.strip().lower()
        meetings = [m for m in meetings if (m.status.value or "").lower() == wanted]
    if min_severity:
        rank = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
        ceiling = rank[min_severity]
        meetings = [m for m in meetings if any(rank[c.severity] <= ceiling for c in m.conflicts)]
    if q:
        needle = q.strip().lower()
        meetings = [m for m in meetings if needle in _haystack(m)]

    start_day = _parse_day(date_from, "date_from")
    end_day = _parse_day(date_to, "date_to")
    if start_day:
        meetings = [m for m in meetings if (_meeting_day(m) or date.max) >= start_day]
    if end_day:
        meetings = [m for m in meetings if (_meeting_day(m) or date.min) <= end_day]

    if sort == "conflicts":
        meetings.sort(key=lambda m: (
            -m.conflict_summary.critical, -m.conflict_summary.high,
            -m.conflict_summary.medium, -m.conflict_summary.total,
            m.start.value or "",
        ))

    return {"count": len(meetings), "meetings": meetings}


@app.get("/api/meetings/{meeting_id}", tags=["meetings"])
def get_meeting(meeting_id: str):
    """One meeting, plus the untouched source records behind it.

    The raw records are included so the UI can show a true side-by-side of what
    each system actually said, not just our interpretation of it.
    """
    result = store.result
    meeting = next((m for m in result.meetings if m.id == meeting_id), None)
    if not meeting:
        raise HTTPException(404, "No meeting with id {}".format(meeting_id))

    raw: dict[str, Any] = {}
    normalized: dict[str, Any] = {}
    for source_ref in meeting.sources:
        event = result.normalized.get(source_ref.record_id)
        if event:
            raw[source_ref.source.value] = event.raw
            normalized[source_ref.source.value] = event
        for absorbed_id in source_ref.absorbed_record_ids:
            absorbed = result.normalized.get(absorbed_id)
            if absorbed:
                raw.setdefault("absorbed", {})[absorbed_id] = absorbed.raw

    return {"meeting": meeting, "raw_records": raw, "normalized_records": normalized}


@app.get("/api/conflicts", tags=["conflicts"])
def list_conflicts(
    severity: Severity | None = Query(None),
    field: str | None = Query(None),
):
    """Every source disagreement in the dataset, flattened.

    This is the direct answer to "where does the data conflict?" -- one list,
    independent of which meeting a conflict happens to belong to.
    """
    conflicts = list(store.result.conflicts)
    if severity:
        conflicts = [c for c in conflicts if c.severity is severity]
    if field:
        conflicts = [c for c in conflicts if c.field == field]

    by_field: dict[str, int] = {}
    for conflict in store.result.conflicts:
        by_field[conflict.field] = by_field.get(conflict.field, 0) + 1

    return {
        "count": len(conflicts),
        "by_severity": store.result.stats.conflicts_by_severity,
        "by_field": by_field,
        "conflicts": conflicts,
    }


@app.get("/api/review-queue", tags=["conflicts"])
def review_queue():
    """Pairs that scored between the review and auto-match thresholds.

    These are the cases the service refuses to decide on its own. An empty
    queue means every pair was either confidently matched or confidently
    rejected at the current thresholds -- not that ambiguity is impossible.
    """
    return {
        "count": len(store.result.review_queue),
        "auto_match_threshold": AUTO_MATCH_THRESHOLD,
        "review_threshold": REVIEW_THRESHOLD,
        "pairs": store.result.review_queue,
    }


@app.get("/api/data-quality", tags=["data quality"])
def data_quality():
    """Everything we repaired, inferred, collapsed, or refused to read."""
    result = store.result
    by_code: dict[str, int] = {}
    for warning in result.warnings:
        by_code[warning.code] = by_code.get(warning.code, 0) + 1

    return {
        "summary": {
            "records_read": result.stats.total_records_read,
            "records_with_warnings": result.stats.records_with_warnings,
            "quarantined": len(result.quarantined),
            "intra_source_duplicates": len(result.duplicates),
        },
        "warnings_by_code": by_code,
        "warnings": result.warnings,
        "quarantined": result.quarantined,
        "duplicates": result.duplicates,
    }


@app.get("/api/sources/{source}/{record_id}", tags=["data quality"])
def get_source_record(source: SourceSystem, record_id: str):
    """The raw and normalized form of a single upstream record."""
    event = store.result.normalized.get(record_id)
    if not event or event.source is not source:
        raise HTTPException(404, "No {} record {}".format(source.value, record_id))
    return {"raw": event.raw, "normalized": event}


@app.post("/api/sync", tags=["meta"])
def trigger_sync():
    """Re-read both feeds and rebuild the reconciled view."""
    result = store.refresh()
    return {"status": "ok", "stats": result.stats}


# --------------------------------------------------------------------------
# Optional: serve the built frontend, so production is a single process
# --------------------------------------------------------------------------
_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(_DIST / "index.html")
