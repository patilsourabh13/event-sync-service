"""Stage 4 -- merge matched records into a unified meeting.

The product requirement is "the user should be able to see where data conflicts
exist between sources", so conflicts are not a by-product here -- they are the
output. Two rules follow from that:

1. No field is ever a bare value. Every field is a FieldValue carrying the
   value we chose, which source it came from, and what the other source said.
2. We never overwrite. The losing value stays attached to the field so the UI
   can show both sides and let a human decide who is right.

Source-of-truth policy (ADR-006): the CRM owns relationship semantics -- who
the client is, who owns them, what the notes say, whether the meeting is still
happening. The calendar owns logistics -- when it starts, who was invited, where
it physically is. Cancellation is the one hard override in either direction.
"""

from __future__ import annotations

import hashlib

from ..config import TIME_CONFLICT_HIGH_MINUTES, TIME_CONFLICT_MEDIUM_MINUTES
from ..models import (
    Candidate,
    Conflict,
    ConflictSummary,
    Coverage,
    FieldValue,
    MatchDecision,
    MeetingStatus,
    Modality,
    NormalizedEvent,
    Severity,
    SourceRef,
    SourceSystem,
    UnifiedMeeting,
)
from .text import containment, normalize_person_name, similarity

# Statuses that describe a finished meeting rather than a contradiction of a
# scheduled one.
_LIFECYCLE_STATUSES = {MeetingStatus.COMPLETED}

_FIELD_LABELS = {
    "title": "Title",
    "start": "Start time",
    "end": "End time",
    "location": "Location",
    "modality": "Meeting format",
    "status": "Status",
    "client_name": "Client",
    "client_company": "Company",
    "owner": "Relationship owner",
    "attendees": "Attendees",
    "notes": "Notes",
}


def meeting_id(record_ids: list[str]) -> str:
    digest = hashlib.sha1("|".join(sorted(record_ids)).encode("utf-8")).hexdigest()
    return "MTG-" + digest[:8]


def _attendee_payload(event: NormalizedEvent | None) -> list[dict] | None:
    if not event or not event.participants:
        return None
    return [
        {
            "name": p.name,
            "email": p.email,
            "company": p.company,
            "is_internal": p.is_internal,
            "role": p.role,
            "raw": p.raw,
            "resolved": bool(p.email),
        }
        for p in event.participants
    ]


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _field(
    name: str,
    crm: NormalizedEvent | None,
    cal: NormalizedEvent | None,
    crm_value,
    cal_value,
    prefer: SourceSystem,
) -> FieldValue:
    """Build a FieldValue, preferring one source but keeping both candidates.

    'Prefer' only decides ties where both sources have a value. If the
    preferred source is empty, the other source fills the gap -- that is a gap
    fill, not a conflict, and is not reported as one.
    """
    candidates: list[Candidate] = []
    if crm and crm_value not in (None, "", []):
        candidates.append(Candidate(source=SourceSystem.CRM, record_id=crm.record_id, value=crm_value))
    if cal and cal_value not in (None, "", []):
        candidates.append(Candidate(source=SourceSystem.CALENDAR, record_id=cal.record_id, value=cal_value))

    if not candidates:
        return FieldValue(field=name, value=None, chosen_from=None, candidates=[])

    chosen = next((c for c in candidates if c.source is prefer), candidates[0])
    return FieldValue(
        field=name,
        value=chosen.value,
        chosen_from=chosen.source,
        candidates=candidates,
        reason=(
            "only {} supplied this field".format(chosen.source.value)
            if len(candidates) == 1
            else "{} is the system of record for this field".format(chosen.source.value)
        ),
    )


# --------------------------------------------------------------------------
# Conflict detectors
# --------------------------------------------------------------------------
def _time_conflict(crm: NormalizedEvent, cal: NormalizedEvent) -> tuple[Severity, str] | None:
    if crm.date_only:
        return None  # the CRM gave no time at all -- the calendar fills the gap
    minutes = abs((crm.start - cal.start).total_seconds()) / 60.0
    if minutes == 0:
        return None
    if minutes > TIME_CONFLICT_HIGH_MINUTES:
        severity = Severity.HIGH
    elif minutes > TIME_CONFLICT_MEDIUM_MINUTES:
        severity = Severity.MEDIUM
    else:
        severity = Severity.LOW
    return severity, (
        "The two systems disagree by {:.0f} minutes on when this meeting starts. "
        "Anyone working from the CRM would arrive at the wrong time.".format(minutes)
    )


def _status_conflict(crm: NormalizedEvent, cal: NormalizedEvent) -> tuple[Severity, str] | None:
    if crm.status is cal.status:
        return None
    if MeetingStatus.CANCELLED in (crm.status, cal.status):
        cancelled_in = "CRM" if crm.status is MeetingStatus.CANCELLED else "calendar"
        live_in = "calendar" if cancelled_in == "CRM" else "CRM"
        return Severity.CRITICAL, (
            "The {} marks this meeting cancelled while the {} still shows it as "
            "live. Someone could show up to a meeting that is not happening."
            .format(cancelled_in, live_in)
        )
    if crm.status in _LIFECYCLE_STATUSES or cal.status in _LIFECYCLE_STATUSES:
        return Severity.LOW, (
            "The CRM tracks completion as a status; the calendar has no such "
            "state. Not a real disagreement, shown for completeness."
        )
    return Severity.MEDIUM, (
        "The two systems hold different scheduling states for this meeting."
    )


def _location_conflict(
    crm: NormalizedEvent, cal: NormalizedEvent, modality_conflicts: bool
) -> tuple[Severity, str] | None:
    if not crm.location or not cal.location:
        return None  # one side is simply missing -- gap fill, not disagreement
    agreement = max(
        containment(crm.location, cal.location), similarity(crm.location, cal.location)
    )
    if agreement >= 0.6:
        return None  # one side is just more specific ("HQ - Room B" / "Room B")
    severity = Severity.HIGH if modality_conflicts else Severity.MEDIUM
    return severity, (
        "The two systems name different places for this meeting."
    )


def _modality_conflict(crm: NormalizedEvent, cal: NormalizedEvent) -> tuple[Severity, str] | None:
    # 'Internal' is a CRM taxonomy for who attends, not a statement about
    # whether the meeting is physical, so it never conflicts with the
    # calendar's inferred format.
    if crm.modality in (Modality.UNKNOWN, Modality.INTERNAL):
        return None
    if cal.modality is Modality.UNKNOWN:
        return None
    if crm.modality is cal.modality:
        return None
    return Severity.HIGH, (
        "The CRM says this is a {} meeting, but the calendar entry points to a "
        "{} one. One of the two will send someone to the wrong place."
        .format(crm.modality.value.replace("_", "-"), cal.modality.value.replace("_", "-"))
    )


def _owner_conflict(crm: NormalizedEvent, cal: NormalizedEvent) -> tuple[Severity, str] | None:
    if not crm.owner or not cal.owner:
        return None
    if normalize_person_name(crm.owner) == normalize_person_name(cal.owner):
        return None
    return Severity.MEDIUM, (
        "The CRM relationship owner is not the person who organized the "
        "calendar event. Often benign (a coordinator booked it), but worth "
        "confirming who owns the relationship."
    )


def _title_conflict(crm: NormalizedEvent, cal: NormalizedEvent) -> tuple[Severity, str] | None:
    if not crm.title or not cal.title:
        return None
    if similarity(crm.title, cal.title) >= 0.5:
        return None
    return Severity.LOW, (
        "The two systems describe this meeting differently. Cosmetic, but it "
        "makes the meeting hard to find by searching one system's wording."
    )


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------
def build_meeting(
    crm: NormalizedEvent | None,
    cal: NormalizedEvent | None,
    match: MatchDecision | None = None,
) -> UnifiedMeeting:
    assert crm or cal, "a unified meeting needs at least one source record"

    if crm and cal:
        coverage = Coverage.BOTH
    elif crm:
        coverage = Coverage.CRM_ONLY
    else:
        coverage = Coverage.CALENDAR_ONLY

    sources: list[SourceRef] = []
    for event in (crm, cal):
        if event:
            sources.append(SourceRef(
                source=event.source,
                record_id=event.record_id,
                absorbed_record_ids=list(event.absorbed_record_ids),
            ))

    ident = meeting_id([s.record_id for s in sources])

    # --- Status, with the cancellation override ---------------------------
    status_field = _field(
        "status", crm, cal,
        crm.status.value if crm else None,
        cal.status.value if cal else None,
        SourceSystem.CRM,
    )
    if crm and cal and MeetingStatus.CANCELLED in (crm.status, cal.status):
        cancelled_source = (
            SourceSystem.CRM if crm.status is MeetingStatus.CANCELLED else SourceSystem.CALENDAR
        )
        status_field.value = MeetingStatus.CANCELLED.value
        status_field.chosen_from = cancelled_source
        status_field.reason = (
            "a cancellation in either system always wins: the cost of treating a "
            "cancelled meeting as live is far higher than the reverse"
        )

    # --- Start time, respecting the CRM's date-only records ---------------
    start_field = _field(
        "start", crm, cal,
        _iso(crm.start) if crm and not crm.date_only else None,
        _iso(cal.start) if cal else None,
        SourceSystem.CALENDAR,
    )
    if start_field.value is None and crm:
        # CRM has a date but no time -- use midnight local and say so.
        start_field.value = _iso(crm.start)
        start_field.chosen_from = SourceSystem.CRM
        start_field.reason = "the CRM supplied a date with no time of day"

    meeting = UnifiedMeeting(
        id=ident,
        title=_field("title", crm, cal,
                     crm.title if crm else None,
                     cal.title if cal else None, SourceSystem.CRM),
        start=start_field,
        end=_field("end", crm, cal, None,
                   _iso(cal.end) if cal else None, SourceSystem.CALENDAR),
        location=_field("location", crm, cal,
                        crm.location if crm else None,
                        cal.location if cal else None, SourceSystem.CALENDAR),
        modality=_field("modality", crm, cal,
                        crm.modality.value if crm else None,
                        cal.modality.value if cal else None, SourceSystem.CALENDAR),
        status=status_field,
        client_name=_field("client_name", crm, cal,
                           crm.client_name if crm else None,
                           cal.client_name if cal else None, SourceSystem.CRM),
        client_company=_field("client_company", crm, cal,
                              crm.client_company if crm else None,
                              cal.client_company if cal else None, SourceSystem.CRM),
        owner=_field("owner", crm, cal,
                     crm.owner if crm else None,
                     cal.owner if cal else None, SourceSystem.CRM),
        attendees=_field("attendees", crm, cal,
                         _attendee_payload(crm), _attendee_payload(cal),
                         SourceSystem.CALENDAR),
        notes=_field("notes", crm, cal,
                     crm.notes if crm else None,
                     cal.notes if cal else None, SourceSystem.CRM),
        coverage=coverage,
        sources=sources,
        is_recurring=bool(cal and cal.is_recurring),
        is_internal=bool((crm and crm.is_internal) or (cal and cal.is_internal)),
        match=match,
        warnings=(crm.warnings if crm else []) + (cal.warnings if cal else []),
    )

    if crm and cal:
        meeting.conflicts = _detect_conflicts(meeting, crm, cal)
        _apply_conflicts_to_fields(meeting)

    meeting.conflict_summary = _summarize(meeting.conflicts)
    return meeting


def _detect_conflicts(
    meeting: UnifiedMeeting, crm: NormalizedEvent, cal: NormalizedEvent
) -> list[Conflict]:
    modality_result = _modality_conflict(crm, cal)

    detectors = [
        ("status", _status_conflict(crm, cal), crm.status.value, cal.status.value),
        ("start", _time_conflict(crm, cal), _iso(crm.start), _iso(cal.start)),
        ("modality", modality_result, crm.modality.value, cal.modality.value),
        ("location", _location_conflict(crm, cal, modality_result is not None),
         crm.location, cal.location),
        ("owner", _owner_conflict(crm, cal), crm.owner, cal.owner),
        ("title", _title_conflict(crm, cal), crm.title, cal.title),
    ]

    conflicts: list[Conflict] = []
    for field_name, result, crm_value, cal_value in detectors:
        if not result:
            continue
        severity, explanation = result
        resolved: FieldValue = getattr(meeting, field_name)
        conflicts.append(Conflict(
            meeting_id=meeting.id,
            meeting_title=meeting.title.value or "(untitled)",
            field=field_name,
            label=_FIELD_LABELS.get(field_name, field_name),
            severity=severity,
            crm_value=crm_value,
            calendar_value=cal_value,
            crm_record_id=crm.record_id,
            calendar_record_id=cal.record_id,
            resolved_value=resolved.value,
            resolved_from=resolved.chosen_from,
            explanation=explanation,
        ))

    order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
    conflicts.sort(key=lambda c: order[c.severity])
    return conflicts


def _apply_conflicts_to_fields(meeting: UnifiedMeeting) -> None:
    """Mirror each conflict onto the field it affects, for the detail view."""
    for conflict in meeting.conflicts:
        field: FieldValue = getattr(meeting, conflict.field)
        field.conflict = True
        field.severity = conflict.severity
        field.reason = conflict.explanation


def _summarize(conflicts: list[Conflict]) -> ConflictSummary:
    summary = ConflictSummary()
    for conflict in conflicts:
        setattr(summary, conflict.severity.value, getattr(summary, conflict.severity.value) + 1)
    summary.total = len(conflicts)
    return summary
