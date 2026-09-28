"""Domain models.

The shape that matters most here is `FieldValue`: no field on a unified meeting
is a bare scalar. Every field carries where it came from, what the other source
said, and whether the two disagree. That is what makes "show me the conflicts"
a data-model property rather than a UI afterthought.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------
class SourceSystem(str, Enum):
    CRM = "crm"
    CALENDAR = "calendar"


class MeetingStatus(str, Enum):
    CONFIRMED = "confirmed"
    SCHEDULED = "scheduled"
    TENTATIVE = "tentative"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class Modality(str, Enum):
    IN_PERSON = "in_person"
    VIRTUAL = "virtual"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Coverage(str, Enum):
    BOTH = "both"
    CRM_ONLY = "crm_only"
    CALENDAR_ONLY = "calendar_only"


# --------------------------------------------------------------------------
# Ingest-stage models
# --------------------------------------------------------------------------
class DataWarning(BaseModel):
    """A field we repaired, inferred, or could not read -- but did not drop."""

    code: str
    message: str
    source: SourceSystem
    record_id: str
    field: str | None = None
    original_value: Any = None
    repaired_value: Any = None


class QuarantinedRecord(BaseModel):
    """A record too broken to place on the timeline. Never silently discarded."""

    source: SourceSystem
    record_id: str | None
    reason: str
    raw: dict[str, Any]


class Participant(BaseModel):
    name: str | None = None
    email: str | None = None
    company: str | None = None
    is_internal: bool = False
    role: Literal["organizer", "owner", "client", "attendee"] = "attendee"
    raw: str | None = None

    @property
    def key(self) -> str | None:
        """Identity used for cross-source people matching."""
        from .pipeline.text import normalize_person_name

        if self.name:
            return normalize_person_name(self.name)
        if self.email:
            return normalize_person_name(self.email.split("@")[0].replace(".", " "))
        return None


class NormalizedEvent(BaseModel):
    """One record from one source, coerced into a single shape."""

    source: SourceSystem
    record_id: str
    title: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    date_only: bool = False          # true when the source gave a date but no time
    location: str | None = None
    modality: Modality = Modality.UNKNOWN
    status: MeetingStatus = MeetingStatus.UNKNOWN
    owner: str | None = None          # CRM relationship owner / calendar organizer
    client_name: str | None = None
    client_company: str | None = None
    participants: list[Participant] = Field(default_factory=list)
    notes: str | None = None
    is_recurring: bool = False
    is_internal: bool = False
    created_at: datetime | None = None
    warnings: list[DataWarning] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    # populated by the intra-source de-duplication stage
    duplicate_of: str | None = None
    absorbed_record_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Reconciliation-stage models
# --------------------------------------------------------------------------
class SignalScore(BaseModel):
    signal: str
    score: float | None          # None => not applicable to this pair
    weight: float
    explanation: str


class MatchDecision(BaseModel):
    crm_record_id: str
    calendar_record_id: str
    score: float
    decision: Literal["auto_matched", "needs_review", "rejected"]
    signals: list[SignalScore]

    @property
    def explanation(self) -> str:
        parts = [s.explanation for s in self.signals if s.score is not None]
        return "; ".join(parts)


class Candidate(BaseModel):
    source: SourceSystem
    record_id: str
    value: Any


class FieldValue(BaseModel):
    """A resolved field plus the full story of how it was resolved."""

    field: str
    value: Any = None
    chosen_from: SourceSystem | None = None
    candidates: list[Candidate] = Field(default_factory=list)
    conflict: bool = False
    severity: Severity | None = None
    reason: str | None = None


class Conflict(BaseModel):
    """A two-sided disagreement, shaped for direct display in the UI."""

    meeting_id: str
    meeting_title: str
    field: str
    label: str
    severity: Severity
    crm_value: Any = None
    calendar_value: Any = None
    crm_record_id: str | None = None
    calendar_record_id: str | None = None
    resolved_value: Any = None
    resolved_from: SourceSystem | None = None
    explanation: str


class ConflictSummary(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    total: int = 0


class SourceRef(BaseModel):
    source: SourceSystem
    record_id: str
    absorbed_record_ids: list[str] = Field(default_factory=list)


class UnifiedMeeting(BaseModel):
    id: str
    title: FieldValue
    start: FieldValue
    end: FieldValue
    location: FieldValue
    modality: FieldValue
    status: FieldValue
    client_name: FieldValue
    client_company: FieldValue
    owner: FieldValue
    attendees: FieldValue
    notes: FieldValue

    coverage: Coverage
    sources: list[SourceRef]
    is_recurring: bool = False
    is_internal: bool = False

    conflicts: list[Conflict] = Field(default_factory=list)
    conflict_summary: ConflictSummary = Field(default_factory=ConflictSummary)
    match: MatchDecision | None = None
    # For single-source meetings: the best partner we considered and rejected.
    # Answers "why is this CRM-only?" without making the user guess.
    closest_candidate: MatchDecision | None = None
    warnings: list[DataWarning] = Field(default_factory=list)

    def fields(self) -> list[FieldValue]:
        return [
            self.title, self.start, self.end, self.location, self.modality,
            self.status, self.client_name, self.client_company, self.owner,
            self.attendees, self.notes,
        ]


class DuplicatePair(BaseModel):
    source: SourceSystem
    kept_record_id: str
    duplicate_record_id: str
    reason: str
    differences: list[str] = Field(default_factory=list)


class SyncStats(BaseModel):
    crm_records_read: int = 0
    calendar_records_read: int = 0
    total_records_read: int = 0
    quarantined: int = 0
    intra_source_duplicates: int = 0
    unified_meetings: int = 0
    matched_both_sources: int = 0
    crm_only: int = 0
    calendar_only: int = 0
    meetings_with_conflicts: int = 0
    total_conflicts: int = 0
    conflicts_by_severity: ConflictSummary = ConflictSummary()
    needs_review: int = 0
    records_with_warnings: int = 0
    generated_at: datetime | None = None


class SyncResult(BaseModel):
    meetings: list[UnifiedMeeting]
    conflicts: list[Conflict]
    review_queue: list[MatchDecision]
    duplicates: list[DuplicatePair]
    warnings: list[DataWarning]
    quarantined: list[QuarantinedRecord]
    normalized: dict[str, NormalizedEvent]
    stats: SyncStats
