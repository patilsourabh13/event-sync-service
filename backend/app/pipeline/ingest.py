"""Stage 1 -- ingest and normalize.

Design rule for this stage: **never drop a record silently.** Anything we
repair emits a DataWarning; anything we genuinely cannot place on a timeline is
quarantined with a reason and served at /api/data-quality. A sync service that
quietly loses rows is worse than one that loudly keeps bad ones.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..config import CALENDAR_FILE, CRM_FILE, INTERNAL_DOMAINS, ORG_TIMEZONE
from ..models import (
    DataWarning,
    MeetingStatus,
    Modality,
    NormalizedEvent,
    Participant,
    QuarantinedRecord,
    SourceSystem,
)
from .text import looks_virtual, title_case_name

ORG_TZ = ZoneInfo(ORG_TIMEZONE)

_STATUS_MAP = {
    "completed": MeetingStatus.COMPLETED,
    "confirmed": MeetingStatus.CONFIRMED,
    "scheduled": MeetingStatus.SCHEDULED,
    "cancelled": MeetingStatus.CANCELLED,
    "canceled": MeetingStatus.CANCELLED,
    "tentative": MeetingStatus.TENTATIVE,
}

_CRM_MODALITY = {
    "in-person": Modality.IN_PERSON,
    "in person": Modality.IN_PERSON,
    "virtual": Modality.VIRTUAL,
    "internal": Modality.INTERNAL,
}

_NON_SPECIFIC_CLIENTS = {"multiple", "various", "tbd", "n/a", "unknown"}

# Matches "raj.patel[at]atlasvc.com" and similar obfuscations.
_AT_SUBSTITUTES = re.compile(r"\s*(\[at\]|\(at\)|\{at\}|\s+at\s+)\s*", re.IGNORECASE)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --------------------------------------------------------------------------
# Low-level parsing
# --------------------------------------------------------------------------
def parse_date_loose(value: Any) -> tuple[date | None, str | None]:
    """Parse a date, salvaging known-bad shapes.

    Returns (date, repair_note). repair_note is set only when we had to do
    something non-obvious to read it -- e.g. CRM-1008's "03-15/2025".
    """
    if not isinstance(value, str) or not value.strip():
        return None, None
    raw = value.strip()

    try:
        return date.fromisoformat(raw), None
    except ValueError:
        pass

    # "03-15/2025" -> MM-DD/YYYY
    m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[/-](\d{4})", raw)
    if m:
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            parsed = date(year, month, day)
            return parsed, "read {!r} as MM-DD/YYYY -> {}".format(raw, parsed)
        except ValueError:
            pass

    # "2025/03/15"
    m = re.fullmatch(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", raw)
    if m:
        try:
            parsed = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return parsed, "read {!r} as YYYY-MM-DD -> {}".format(raw, parsed)
        except ValueError:
            pass

    return None, None


def parse_time_loose(value: Any) -> time | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


def parse_timestamp_loose(value: Any) -> tuple[datetime | None, list[str]]:
    """Parse an ISO-ish timestamp into an org-local aware datetime.

    Returned notes describe anything a reviewer should know: an explicit UTC
    offset that we converted, or a precision that differs from its siblings.
    """
    notes: list[str] = []
    if not isinstance(value, str) or not value.strip():
        return None, notes
    raw = value.strip()

    had_zone = bool(re.search(r"(Z|[+-]\d{2}:?\d{2})$", raw))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", raw):
        notes.append(
            "{!r} omits seconds while sibling records in the same feed "
            "include them".format(raw)
        )

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, notes

    if parsed.tzinfo is None:
        # Naive timestamps are assumed to be org-local already. See ADR-003.
        return parsed.replace(tzinfo=ORG_TZ), notes

    local = parsed.astimezone(ORG_TZ)
    if had_zone:
        notes.append(
            "{!r} carried an explicit UTC offset while sibling records are naive "
            "local; converted to {} -> {}".format(
                raw, ORG_TIMEZONE, local.strftime("%Y-%m-%d %H:%M")
            )
        )
    return local, notes


def repair_email(raw: Any) -> tuple[str | None, str | None]:
    """Return (email, repair_note); email is None when it isn't one at all."""
    if not isinstance(raw, str) or not raw.strip():
        return None, None
    candidate = raw.strip()
    if _EMAIL_RE.match(candidate):
        return candidate.lower(), None
    repaired = _AT_SUBSTITUTES.sub("@", candidate)
    if _EMAIL_RE.match(repaired):
        return repaired.lower(), "repaired {!r} -> {!r}".format(candidate, repaired.lower())
    return None, None


def participant_from_email(raw: Any) -> tuple[Participant, str | None, bool]:
    """Build a Participant from a calendar attendee string.

    Returns (participant, repair_note, is_resolvable).
    """
    email, note = repair_email(raw)
    if not email:
        # e.g. "external-guests" -- a real entry we cannot resolve to a person.
        return Participant(name=None, email=None, raw=str(raw)), None, False

    local_part, domain = email.split("@", 1)
    is_internal = domain in INTERNAL_DOMAINS
    participant = Participant(
        name=title_case_name(local_part),
        email=email,
        company=None if is_internal else domain.rsplit(".", 1)[0],
        is_internal=is_internal,
        raw=str(raw),
    )
    return participant, note, True


# --------------------------------------------------------------------------
# Source adapters
# --------------------------------------------------------------------------
def normalize_crm_record(
    record: dict[str, Any],
) -> tuple[NormalizedEvent | None, list[DataWarning], QuarantinedRecord | None]:
    warnings: list[DataWarning] = []
    record_id = record.get("crm_id")

    if not record_id:
        return None, warnings, QuarantinedRecord(
            source=SourceSystem.CRM,
            record_id=None,
            reason="record has no crm_id, so it cannot be referenced or reconciled",
            raw=record,
        )

    def warn(code, message, field=None, original=None, repaired=None):
        warnings.append(DataWarning(
            code=code, message=message, source=SourceSystem.CRM,
            record_id=record_id, field=field,
            original_value=original, repaired_value=repaired,
        ))

    meeting_date, date_note = parse_date_loose(record.get("meeting_date"))
    if meeting_date is None:
        return None, warnings, QuarantinedRecord(
            source=SourceSystem.CRM,
            record_id=record_id,
            reason="unreadable meeting_date {!r}; a meeting without a date cannot "
                   "be placed on a timeline".format(record.get("meeting_date")),
            raw=record,
        )
    if date_note:
        warn("date_salvaged", "Malformed meeting_date: " + date_note,
             "meeting_date", record.get("meeting_date"), str(meeting_date))

    meeting_time = parse_time_loose(record.get("meeting_time"))
    date_only = meeting_time is None
    if date_only:
        warn("missing_time",
             "No meeting_time; the CRM gives a date only, so matching falls back "
             "to same-day comparison.", "meeting_time", record.get("meeting_time"))
        start = datetime.combine(meeting_date, time(0, 0), tzinfo=ORG_TZ)
    else:
        start = datetime.combine(meeting_date, meeting_time, tzinfo=ORG_TZ)

    raw_type = (record.get("meeting_type") or "").strip().lower()
    modality = _CRM_MODALITY.get(raw_type, Modality.UNKNOWN)
    is_internal = modality is Modality.INTERNAL

    raw_status = (record.get("status") or "").strip().lower()
    status = _STATUS_MAP.get(raw_status, MeetingStatus.UNKNOWN)
    if status is MeetingStatus.UNKNOWN and record.get("status"):
        warn("unknown_status", "Unrecognized status {!r}".format(record.get("status")),
             "status", record.get("status"))

    location = record.get("location") or None
    if not location:
        warn("missing_location", "No location on the CRM record.", "location")

    owner = record.get("relationship_owner") or None
    client_name = record.get("client_name") or None
    client_company = record.get("client_company") or None

    participants: list[Participant] = []
    if owner:
        participants.append(
            Participant(name=owner, role="owner", is_internal=True, raw=owner)
        )

    if client_name and client_name.strip().lower() in _NON_SPECIFIC_CLIENTS:
        warn("non_specific_client",
             "client_name is {!r} rather than a named person; treated as "
             "'no identifiable individual' for matching.".format(client_name),
             "client_name", client_name)
        client_name = None

    if client_name:
        participants.append(Participant(
            name=client_name, company=client_company, role="client", raw=client_name,
        ))
    elif not is_internal and not client_company:
        warn("missing_client",
             "No client_name or client_company on a non-internal meeting.",
             "client_name")

    return (
        NormalizedEvent(
            source=SourceSystem.CRM,
            record_id=record_id,
            title=record.get("subject") or None,
            start=start,
            end=None,  # the CRM feed carries no end time at all
            date_only=date_only,
            location=location,
            modality=modality,
            status=status,
            owner=owner,
            client_name=client_name,
            client_company=client_company,
            participants=participants,
            notes=record.get("notes") or None,
            is_recurring=False,
            is_internal=is_internal,
            created_at=parse_timestamp_loose(record.get("created_at"))[0],
            warnings=warnings,
            raw=record,
        ),
        warnings,
        None,
    )


def normalize_calendar_record(
    record: dict[str, Any],
) -> tuple[NormalizedEvent | None, list[DataWarning], QuarantinedRecord | None]:
    warnings: list[DataWarning] = []
    record_id = record.get("event_id")

    if not record_id:
        return None, warnings, QuarantinedRecord(
            source=SourceSystem.CALENDAR,
            record_id=None,
            reason="record has no event_id, so it cannot be referenced or reconciled",
            raw=record,
        )

    def warn(code, message, field=None, original=None, repaired=None):
        warnings.append(DataWarning(
            code=code, message=message, source=SourceSystem.CALENDAR,
            record_id=record_id, field=field,
            original_value=original, repaired_value=repaired,
        ))

    start, start_notes = parse_timestamp_loose(record.get("start_time"))
    for note in start_notes:
        code = "timezone_normalized" if "UTC offset" in note else "timestamp_format"
        warn(code, note, "start_time", record.get("start_time"),
             start.isoformat() if start else None)

    if start is None:
        return None, warnings, QuarantinedRecord(
            source=SourceSystem.CALENDAR,
            record_id=record_id,
            reason="unreadable start_time {!r}; an event without a start cannot be "
                   "placed on a timeline".format(record.get("start_time")),
            raw=record,
        )

    end, end_notes = parse_timestamp_loose(record.get("end_time"))
    for note in end_notes:
        code = "timezone_normalized" if "UTC offset" in note else "timestamp_format"
        warn(code, note, "end_time", record.get("end_time"),
             end.isoformat() if end else None)
    if record.get("end_time") and end is None:
        warn("unreadable_end_time",
             "Could not read end_time {!r}; kept the event with an unknown "
             "duration.".format(record.get("end_time")), "end_time",
             record.get("end_time"))
    if end and end < start:
        warn("end_before_start", "end_time precedes start_time; dropped the end.",
             "end_time", record.get("end_time"))
        end = None

    participants: list[Participant] = []
    seen: set[str] = set()
    owner_name = None

    organizer_raw = record.get("organizer")
    if organizer_raw:
        organizer, note, resolvable = participant_from_email(organizer_raw)
        if note:
            warn("malformed_email", "Organizer email " + note, "organizer",
                 organizer_raw, organizer.email)
        if resolvable:
            organizer.role = "organizer"
            owner_name = organizer.name
            participants.append(organizer)
            seen.add(organizer.email or str(organizer_raw))
        else:
            warn("unparseable_organizer",
                 "Organizer {!r} is not an email address.".format(organizer_raw),
                 "organizer", organizer_raw)

    attendees = record.get("attendees")
    if attendees is None:
        attendees = []
    if not isinstance(attendees, list):
        warn("malformed_attendees", "attendees was not a list; ignored.",
             "attendees", attendees)
        attendees = []
    if not attendees:
        warn("no_attendees",
             "Event has no attendees, so participant identity cannot contribute "
             "to matching for this record.", "attendees")

    for raw_attendee in attendees:
        participant, note, resolvable = participant_from_email(raw_attendee)
        if note:
            warn("malformed_email", "Attendee email " + note, "attendees",
                 raw_attendee, participant.email)
        if not resolvable:
            warn("unparseable_attendee",
                 "Attendee {!r} is not an email address; kept on the record but "
                 "unusable for identity matching.".format(raw_attendee),
                 "attendees", raw_attendee)
            participants.append(participant)
            continue
        key = participant.email or str(raw_attendee)
        if key in seen:
            continue
        seen.add(key)
        participants.append(participant)

    location = record.get("location") or None
    if not location:
        warn("missing_location", "No location on the calendar event.", "location")

    resolvable_people = [p for p in participants if p.email]
    is_internal = bool(resolvable_people) and all(p.is_internal for p in resolvable_people)

    if looks_virtual(location):
        modality = Modality.VIRTUAL
    elif not location:
        modality = Modality.UNKNOWN
    else:
        modality = Modality.IN_PERSON

    externals = [p for p in resolvable_people if not p.is_internal]
    client_name = externals[0].name if externals else None
    client_company = externals[0].company if externals else None

    raw_status = (record.get("status") or "").strip().lower()
    status = _STATUS_MAP.get(raw_status, MeetingStatus.UNKNOWN)
    if status is MeetingStatus.UNKNOWN and record.get("status"):
        warn("unknown_status", "Unrecognized status {!r}".format(record.get("status")),
             "status", record.get("status"))

    description = record.get("description") or None
    if not description:
        warn("missing_description", "No description on the calendar event.",
             "description")

    return (
        NormalizedEvent(
            source=SourceSystem.CALENDAR,
            record_id=record_id,
            title=record.get("title") or None,
            start=start,
            end=end,
            date_only=False,
            location=location,
            modality=modality,
            status=status,
            owner=owner_name,
            client_name=client_name,
            client_company=client_company,
            participants=participants,
            notes=description,
            is_recurring=bool(record.get("is_recurring")),
            is_internal=is_internal,
            created_at=parse_timestamp_loose(record.get("created_at"))[0],
            warnings=warnings,
            raw=record,
        ),
        warnings,
        None,
    )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def _load_json_array(
    path: Path, source: SourceSystem
) -> tuple[list[dict], list[QuarantinedRecord]]:
    if not path.exists():
        raise FileNotFoundError(
            "Missing data file for {}: {}".format(source.value, path)
        )
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("{} must contain a JSON array".format(path))

    records: list[dict] = []
    quarantined: list[QuarantinedRecord] = []
    for index, item in enumerate(payload):
        if isinstance(item, dict):
            records.append(item)
        else:
            quarantined.append(QuarantinedRecord(
                source=source,
                record_id=None,
                reason="array element {} is {}, not an object".format(
                    index, type(item).__name__
                ),
                raw={"value": item},
            ))
    return records, quarantined


def ingest_all(
    crm_path: Path | None = None,
    calendar_path: Path | None = None,
) -> tuple[list[NormalizedEvent], list[DataWarning], list[QuarantinedRecord], dict[str, int]]:
    crm_path = crm_path or CRM_FILE
    calendar_path = calendar_path or CALENDAR_FILE

    crm_raw, quarantined = _load_json_array(crm_path, SourceSystem.CRM)
    calendar_raw, calendar_quarantined = _load_json_array(calendar_path, SourceSystem.CALENDAR)
    quarantined.extend(calendar_quarantined)

    events: list[NormalizedEvent] = []
    warnings: list[DataWarning] = []

    for record in crm_raw:
        event, record_warnings, bad = normalize_crm_record(record)
        warnings.extend(record_warnings)
        if bad:
            quarantined.append(bad)
        elif event:
            events.append(event)

    for record in calendar_raw:
        event, record_warnings, bad = normalize_calendar_record(record)
        warnings.extend(record_warnings)
        if bad:
            quarantined.append(bad)
        elif event:
            events.append(event)

    counts = {"crm": len(crm_raw), "calendar": len(calendar_raw)}
    return events, warnings, quarantined, counts
