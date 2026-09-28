# Decision log

Every entry is a judgment call the brief deliberately left open. Each records
what was decided, why, and what the reasonable alternative was — so a reviewer
can disagree with the reasoning rather than reverse-engineer it from code.

---

<a id="adr-001"></a>

## ADR-001 — Never drop a record silently

**Decision.** Malformed input is repaired and flagged, never discarded. A
record that genuinely cannot be placed on a timeline (no id, or an unreadable
date) is *quarantined* with a reason and served at `/api/data-quality`.

**Why.** A sync service that quietly loses rows is worse than one that loudly
keeps bad ones: the first failure mode is invisible, the second is fixable.
Every repair emits a `DataWarning` carrying the original value, the repaired
value, and a human-readable note.

**Consequence.** On the supplied data **nothing is quarantined** — every defect
turned out to be recoverable. The quarantine path is still built and tested,
because the next file will not be so cooperative.

*Alternative rejected:* skip unparseable records. Faster, and silently wrong.

---

<a id="adr-002"></a>

## ADR-002 — Salvage `"03-15/2025"` as `MM-DD/YYYY`

**Decision.** `CRM-1008`'s date is read as 15 March 2025.

**Why.** The rest of the CRM feed is unambiguously US-formatted and this record
sits in a mid-March cluster. Reading it as DD-MM would give 3 May, which is
outside every other date in the file.

**Consequence.** The record matches `CAL-A9` (Atlas Ventures lunch, 15 March),
which independently confirms the reading.

*Alternative rejected:* quarantine it. Defensible, but it would discard a
meeting we can recover with high confidence — and the corroborating calendar
entry makes the inference safe rather than merely convenient.

---

<a id="adr-003"></a>

## ADR-003 — Assume `America/New_York`; convert the one UTC stamp honestly

**Decision.** Naive timestamps are treated as already being in the firm's local
timezone. `CAL-A4`'s `2025-03-13T19:00:00Z` — the only zoned value in either
feed — is converted, giving **15:00 EDT**.

**Why.** 13 March 2025 falls after US daylight saving began (9 March), so the
correct offset is −04:00, not −05:00.

**Consequence.** The CRM says 14:00 and the calendar now says 15:00, so a
1-hour `medium` time conflict is reported.

**This is deliberate, and it is the most arguable call here.** Assuming EST
(−05:00) would make the two line up *exactly*, which is very likely what the
fixture author intended. That was rejected: picking the timezone that makes the
data agree is choosing the answer first and the reasoning second. The service
is correct about the calendar and honest about the resulting disagreement —
and a 1-hour discrepancy on a due-diligence meeting is exactly the kind of
thing a user should be told about rather than have smoothed away.

*Alternative rejected:* treat the `Z` as decorative and strip it. Would produce
a silently wrong 19:00 meeting.

---

<a id="adr-004"></a>

## ADR-004 — De-duplicate within a source before matching across sources

**Decision.** Stage 2 collapses duplicates inside each feed; stage 3 matches
across feeds.

**Why.** `CAL-A5` and `CAL-A6` are the same Pinnacle meeting recorded twice. If
both survived into matching, `CRM-1005` would pair with one and the other would
appear as a phantom "calendar-only" meeting that never existed.

**The rule:** same source, **same calendar day**, start times ≤ 60 minutes
apart, ≥ 50% attendee overlap, ≥ 35% title similarity.

**The same-day gate is load-bearing.** `CAL-A3` and `CAL-A18` are byte-for-byte
similar — same title, same attendees, same `created_at` — but are two
occurrences of a recurring series a week apart. Requiring the same day
separates them from `A5`/`A6` cleanly. A `is_recurring` guard backs it up and
documents the intent.

**Survivor:** the most recently created record. `CAL-A6` (created 10 March)
beats `CAL-A5` (created 2 March) and happens to include Sandra Mills, the CIO
the CRM note predicted would attend — the later row really was better
information.

*Alternative rejected:* dedupe on `(title, attendees)` alone. Collapses the
recurring series and loses a real meeting.

---

<a id="adr-005"></a>

## ADR-005 — Weighted, explainable signals with renormalization

**Decision.** Pairs are scored 0–1 across five weighted signals. Any signal
that cannot be evaluated returns *not applicable*, and the weights are
renormalized over the remaining ones.

| Signal | Weight |
|---|---|
| Participant identity | 0.35 |
| Time proximity | 0.28 |
| Title similarity | 0.17 |
| Owner / organizer | 0.10 |
| Location agreement | 0.10 |

**Why renormalize.** Scoring a missing field as `0.0` punishes a pair for
incomplete data. `CRM-1007` has no location at all; without renormalization it
would be penalised for a fact about the CRM's schema rather than about the
meeting.

**Why these weights.** Participant identity is the only near-unique key
available — attendee emails resolve to names (`david.park@meridiancap.com` →
"david park") that compare directly against `client_name`. Title is weighted
low because `CRM-1011` "Annual Allocation Review" and `CAL-A12` "Horizon Wealth
- Year-End Review" are the same meeting with almost no shared words. Organizer
is weighted lowest because `CRM-1013` proves a coordinator often books on the
owner's behalf.

**Explainability is a hard requirement.** Every pair retains its per-signal
breakdown, served to the UI. "Why were these merged?" is always answerable.

*Alternative rejected:* a fuzzy-matching library or an ML model. On 42 records
the first hides the logic under review and the second is unjustifiable.

---

<a id="adr-006"></a>

## ADR-006 — Source of truth is per-field, not per-source

**Decision.**

| Fields | Winner | Reasoning |
|---|---|---|
| `status`, `client`, `owner`, `notes`, `title` | **CRM** | System of record for the client relationship |
| `start`, `end`, `location`, `modality`, `attendees` | **Calendar** | System of record for logistics |
| Cancellation | **Whichever source reports it** | Hard override, both directions |

**Why split it.** "The CRM is always right" fails on times — the calendar is
what people's devices actually alert from. "The calendar is always right" fails
on cancellation — `CRM-1009` is cancelled while `CAL-A10` is stale.

**On `CRM-1002` specifically** (CRM `In-Person` @ "NYC Office - 30th Floor"
vs calendar `Zoom - https://zoom.us/j/98765432100`): the calendar wins. The
CRM's value is a dropdown selection; the calendar's is a working join URL. A
concrete artefact beats a categorisation. Both values remain visible and the
conflict is flagged `high` — the service states a preference, it does not
pretend to certainty.

**The cancellation override is asymmetric on purpose.** Treating a cancelled
meeting as live wastes someone's afternoon; treating a live meeting as
cancelled is caught the moment anyone looks. The costs are not symmetric, so
the rule is not either.

---

<a id="adr-007"></a>

## ADR-007 — Three match tiers, with a review queue

**Decision.** ≥ 0.70 merge · 0.45–0.70 review queue · < 0.45 unrelated.
A same-day hard gate overrides everything.

**Why.** Two tiers force a guess on every borderline pair. The middle band lets
the service say "these might be the same meeting, a human should decide" —
which is the honest response to an underspecified problem.

**Result on this data: the review band is empty.** Every pair scored either
above 0.74 or below 0.40. That is reported as-is in the UI, with the thresholds
shown, rather than tuning the band until it looks busy. The mechanism exists for
the data that will need it.

**Assignment is greedy and one-to-one:** score all same-day pairs, take them
best-first, skip records already claimed. Guarantees no record is merged twice
and the strongest evidence wins when pairs compete.

**Unmatched records still get an explanation.** Each single-source meeting
carries the best candidate it was compared against and rejected, so
"why is this CRM-only?" is answerable rather than assumed.

*Alternative rejected:* global optimal assignment (Hungarian algorithm).
Correct, but on this data it produces identical output with materially less
readable code.

---

<a id="adr-008"></a>

## ADR-008 — Conflict severity reflects consequence, not field type

**Decision.** Four levels, assigned by what happens if a user acts on the wrong
value.

| Severity | Meaning | Instances found |
|---|---|---|
| `critical` | Someone acts on false information | 1 — cancelled vs confirmed |
| `high` | Someone goes to the wrong place or time | 3 — Zoom vs in-person, location, 2h drift |
| `medium` | Needs confirming, unlikely to harm | 4 — 30–60min drift, owner mismatch |
| `low` | Cosmetic | 9 — differing titles, lifecycle status |

**Three rules suppress false alarms**, because a conflict view nobody trusts is
worse than none:

1. **Missing ≠ conflicting.** `CRM-1007` has no time; `CAL-A8` supplies 15:00.
   That is a gap fill, reported as provenance, not as a disagreement.
2. **More specific ≠ different.** "HQ - Conference Room B" and "Conference
   Room B" are the same room. Locations are compared by token containment, so
   one side being more precise is agreement.
3. **Lifecycle ≠ contradiction.** CRM `completed` vs calendar `confirmed` is
   not a disagreement — the calendar has no completion state. Flagged `low`
   and explained, rather than dropped or escalated.

---

<a id="adr-009"></a>

## ADR-009 — Stable, content-derived meeting ids

**Decision.** `MTG-` + the first 8 hex characters of a SHA-1 over the sorted
source record ids.

**Why.** Deterministic across restarts without a database, so a URL or a
bookmark survives a re-sync. Sequential ids would shift whenever an upstream
record was added.

**Trade-off.** If a meeting's matching changes, its id changes — correct here,
since a differently-composed meeting *is* a different reconciliation, but it
would need a stable surrogate key if this service ever owned persisted state.

---

<a id="adr-010"></a>

## ADR-010 — FastAPI + React, two processes, one command

**Decision.** FastAPI backend, React/Vite frontend, `./start.sh` or
`.\start.ps1` to run both.

**Why FastAPI.** Pydantic makes the "every field carries its provenance" model
enforceable rather than conventional, and `/docs` gives a reviewer an
interactive API without extra work.

**Why plain React with no UI library.** Design is explicitly not evaluated;
adding a component framework would mean more install surface and no more
information on screen.

**One command, two processes.** The script creates the virtualenv, installs
both dependency sets on first run, starts the API and the dev server, and
stops both on Ctrl+C. If `frontend/dist` exists, the API also serves the built
UI at `http://127.0.0.1:8000`, so production is a genuinely single process.
