"""Stage 3 -- cross-source matching.

A transparent weighted-signal score, not a black box. Every pair keeps the
per-signal breakdown that produced its number, and that breakdown is served to
the UI, so "why did these two records get merged?" is always answerable.

Signals return None when they are not applicable to a pair (a field is missing
on one side). Weights are then renormalized over the applicable signals only,
so a null location dilutes the score instead of scoring zero against it.
"""

from __future__ import annotations

from ..config import (
    AUTO_MATCH_THRESHOLD,
    REVIEW_THRESHOLD,
    SAME_DAY_HARD_GATE,
    SIGNAL_WEIGHTS,
)
from ..models import MatchDecision, NormalizedEvent, SignalScore, SourceSystem
from .text import (
    common_prefix_len,
    company_key,
    containment,
    normalize_person_name,
    similarity,
    tokens,
)

# Shortest common prefix that counts as a company/email-domain match.
# 'meridiancap' vs 'meridiancapital' -> 11, 'horizonwp' vs
# 'horizonwealthpartners' -> 8. Six is the floor that admits those without
# letting unrelated names collide.
MIN_DOMAIN_PREFIX = 6


# --------------------------------------------------------------------------
# Individual signals
# --------------------------------------------------------------------------
def _person_keys(event: NormalizedEvent) -> set[str]:
    return {
        normalize_person_name(p.name)
        for p in event.participants
        if p.name and normalize_person_name(p.name)
    }


def _domains(event: NormalizedEvent) -> set[str]:
    return {p.company for p in event.participants if p.company}


def _company_matches_domain(company: str | None, domains: set[str]) -> bool:
    key = company_key(company)
    if not key:
        return False
    for domain in domains:
        domain_key = company_key(domain)
        if not domain_key:
            continue
        if common_prefix_len(key, domain_key) >= min(
            MIN_DOMAIN_PREFIX, len(key), len(domain_key)
        ):
            return True
    return False


def participant_signal(crm: NormalizedEvent, cal: NormalizedEvent) -> tuple[float | None, str]:
    """Do these two records involve the same people?

    The strongest available evidence. Calendar attendee emails resolve to
    person names ('david.park@meridiancap.com' -> 'david park') which compare
    directly against the CRM's client_name.
    """
    cal_people = _person_keys(cal)
    cal_domains = _domains(cal)
    resolvable = [p for p in cal.participants if p.email]

    if not resolvable:
        return None, "no resolvable attendees on the calendar event"

    crm_client = normalize_person_name(crm.client_name) if crm.client_name else ""

    if crm_client:
        if crm_client in cal_people:
            return 1.0, "client '{}' is on the calendar invite".format(crm.client_name)
        if _company_matches_domain(crm.client_company, cal_domains):
            return 0.75, "no name match, but an attendee's email domain matches '{}'".format(
                crm.client_company
            )
        return 0.0, "CRM client '{}' is not among the calendar attendees".format(
            crm.client_name
        )

    if crm.is_internal:
        if cal.is_internal:
            return 0.7, "both records describe an internal-only meeting"
        return 0.0, "CRM says internal, but the calendar event has external attendees"

    # CRM names a company but no individual (e.g. client_name "Multiple").
    if crm.client_company:
        if _company_matches_domain(crm.client_company, cal_domains):
            return 0.75, "an attendee's email domain matches '{}'".format(crm.client_company)
        overlap = containment(crm.client_company, cal.title)
        if overlap > 0:
            return 0.6, "'{}' appears in the calendar event title".format(crm.client_company)
        return 0.0, "no shared people or company between the records"

    return None, "the CRM record identifies no client or company"


def time_signal(crm: NormalizedEvent, cal: NormalizedEvent) -> tuple[float | None, str]:
    """How close are the two start times, after timezone normalization?"""
    if crm.date_only:
        if crm.start.date() == cal.start.date():
            return 0.8, "same day (the CRM record has no time of day)"
        return 0.0, "different days"

    minutes = abs((crm.start - cal.start).total_seconds()) / 60.0
    if minutes == 0:
        return 1.0, "start times match exactly"

    for limit, score in ((15, 0.95), (30, 0.85), (60, 0.70), (120, 0.55), (240, 0.35)):
        if minutes <= limit:
            return score, "start times are {:.0f} minutes apart".format(minutes)

    if crm.start.date() == cal.start.date():
        return 0.2, "same day but {:.0f} minutes apart".format(minutes)
    return 0.0, "different days"


def title_signal(crm: NormalizedEvent, cal: NormalizedEvent) -> tuple[float | None, str]:
    """Title similarity, with a boost when the client's company is in the title.

    The boost matters: the CRM calls one meeting 'Annual Allocation Review'
    while the calendar calls it 'Horizon Wealth - Year-End Review'. Those share
    almost no words, but the calendar title carries the client's company name,
    which is strong evidence they are the same meeting.
    """
    if not crm.title or not cal.title:
        return None, "one record has no title"

    base = similarity(crm.title, cal.title)
    company_overlap = containment(crm.client_company, cal.title) if crm.client_company else 0.0

    if company_overlap > 0:
        boosted = 0.5 + 0.4 * company_overlap
        if boosted > base:
            return boosted, "titles differ, but '{}' appears in the calendar title".format(
                crm.client_company
            )
    return base, "titles are {:.0%} similar".format(base)


def owner_signal(crm: NormalizedEvent, cal: NormalizedEvent) -> tuple[float | None, str]:
    """Is the CRM relationship owner running or attending the calendar event?

    Weighted low on purpose: a coordinator often organizes on someone else's
    behalf (CRM-1013 is owned by Sarah Chen but organized by Priya Sharma).
    """
    if not crm.owner:
        return None, "no relationship owner on the CRM record"

    owner = normalize_person_name(crm.owner)
    organizer = normalize_person_name(cal.owner) if cal.owner else ""

    if organizer and owner == organizer:
        return 1.0, "relationship owner '{}' organized the event".format(crm.owner)
    if owner in _person_keys(cal):
        return 0.6, "relationship owner '{}' attends but did not organize".format(crm.owner)
    if not organizer:
        return None, "no organizer on the calendar event"
    return 0.0, "relationship owner '{}' is not on the invite".format(crm.owner)


def location_signal(crm: NormalizedEvent, cal: NormalizedEvent) -> tuple[float | None, str]:
    """Location agreement, tolerant of one side being more specific.

    Containment rather than equality, because the CRM writes 'HQ - Conference
    Room B' where the calendar writes 'Conference Room B', and 'NYC Office'
    where the calendar writes 'NYC Office - 12th Floor'. Those agree.
    """
    if not crm.location or not cal.location:
        return None, "one record has no location"

    score = max(containment(crm.location, cal.location), similarity(crm.location, cal.location))
    if score >= 0.6:
        return score, "locations agree ({!r} / {!r})".format(crm.location, cal.location)
    return score, "locations differ ({!r} vs {!r})".format(crm.location, cal.location)


_SIGNALS = {
    "participants": participant_signal,
    "time": time_signal,
    "title": title_signal,
    "owner": owner_signal,
    "location": location_signal,
}


# --------------------------------------------------------------------------
# Scoring and assignment
# --------------------------------------------------------------------------
def score_pair(crm: NormalizedEvent, cal: NormalizedEvent) -> MatchDecision:
    signals: list[SignalScore] = []
    weighted_total = 0.0
    applicable_weight = 0.0

    for name, fn in _SIGNALS.items():
        score, explanation = fn(crm, cal)
        weight = SIGNAL_WEIGHTS[name]
        signals.append(SignalScore(
            signal=name, score=score, weight=weight, explanation=explanation,
        ))
        if score is not None:
            weighted_total += score * weight
            applicable_weight += weight

    total = weighted_total / applicable_weight if applicable_weight else 0.0

    if SAME_DAY_HARD_GATE and crm.start.date() != cal.start.date():
        total = 0.0

    if total >= AUTO_MATCH_THRESHOLD:
        decision = "auto_matched"
    elif total >= REVIEW_THRESHOLD:
        decision = "needs_review"
    else:
        decision = "rejected"

    return MatchDecision(
        crm_record_id=crm.record_id,
        calendar_record_id=cal.record_id,
        score=round(total, 4),
        decision=decision,
        signals=signals,
    )


def match_events(
    events: list[NormalizedEvent],
) -> tuple[list[MatchDecision], list[MatchDecision]]:
    """Pair CRM records with calendar records, one-to-one.

    Greedy best-first: score every same-day pair, then take matches in
    descending score order, skipping records already claimed. That guarantees a
    CRM record cannot bind to two calendar events, and that the strongest
    evidence wins when several pairs compete.

    Returns (auto_matched, needs_review). The review queue deliberately excludes
    pairs whose records were confidently matched elsewhere -- a leftover
    near-miss against an already-resolved record is noise, not ambiguity.
    """
    crm_events = [e for e in events if e.source is SourceSystem.CRM]
    cal_events = [e for e in events if e.source is SourceSystem.CALENDAR]

    scored: list[MatchDecision] = []
    for crm in crm_events:
        for cal in cal_events:
            if SAME_DAY_HARD_GATE and crm.start.date() != cal.start.date():
                continue
            decision = score_pair(crm, cal)
            if decision.decision != "rejected":
                scored.append(decision)

    scored.sort(key=lambda d: (-d.score, d.crm_record_id, d.calendar_record_id))

    claimed_crm: set[str] = set()
    claimed_cal: set[str] = set()
    auto_matched: list[MatchDecision] = []
    leftovers: list[MatchDecision] = []

    for decision in scored:
        if decision.crm_record_id in claimed_crm or decision.calendar_record_id in claimed_cal:
            continue
        if decision.decision == "auto_matched":
            claimed_crm.add(decision.crm_record_id)
            claimed_cal.add(decision.calendar_record_id)
            auto_matched.append(decision)
        else:
            leftovers.append(decision)

    review_queue = [
        d for d in leftovers
        if d.crm_record_id not in claimed_crm and d.calendar_record_id not in claimed_cal
    ]
    review_queue.sort(key=lambda d: -d.score)

    return auto_matched, review_queue
