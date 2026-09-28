# Brainstorming — data forensics and approach

*This is the working document from the planning session, before any code was
written. It is included because the brief asks for the AI-collaborated
documentation that fed the development process. See
[03-ai-collaboration.md](03-ai-collaboration.md) for how that session was run.*

---

## 1. Reading the brief

The brief has one line that shapes everything else:

> We have intentionally not told you how to handle any of these cases.

Combined with *"the quality of your README matters as much as the quality of
your code"* and *"we are not evaluating visual design, CSS polish, performance
optimization, or test coverage"*, this is not primarily a coding exercise. The
work is:

1. Notice the landmines in the data without being told where they are.
2. Make a defensible call on each one.
3. Write down **why**, so a reviewer can disagree with the decision rather than
   guess at it.

The functional requirement that follows from this is
*"the user should be able to see where data conflicts exist between sources"*.
That is the actual product. A reconciled list that silently picks a winner and
throws the loser away would satisfy the letter of the brief and miss its point.

**Design principle adopted:** *every reconciliation decision must be
inspectable, and no value is ever discarded.*

---

## 2. Data forensics

Before designing anything, both files were read in full and every record
cross-referenced by hand. 20 CRM records, 22 calendar records, 42 total.

### 2.1 The clean pairs

`CRM-1001↔CAL-A1`, `1006↔A7`, `1012↔A13`, `1014↔A15`, `1017↔A20`,
`1018↔A21`, `1019↔A22` — same client, same day, same time. These validate a
matcher but do not test it.

### 2.2 The deliberate landmines

| # | Records | Defect | Why it matters |
|---|---|---|---|
| 1 | `CRM-1008` | `meeting_date: "03-15/2025"` | Not ISO. Must be salvaged or it never pairs with `CAL-A9`. |
| 2 | `CAL-A4` | `start_time: "2025-03-13T19:00:00Z"` — the **only** record with a `Z`; every other calendar time is naive-local | CRM says `14:00`. Naive string comparison fails. Forces an explicit timezone policy. |
| 3 | `CAL-A5` / `CAL-A6` | Both Pinnacle/Kevin O'Brien, 2025-03-17, `11:00` vs `11:30` | **The intra-source duplicate.** If not collapsed first, `CRM-1005` competes for two calendar partners and one is stranded as a phantom "calendar-only" meeting. |
| 4 | `CAL-A3` / `CAL-A18` | Identical title, attendees and `created_at`; `is_recurring: true`; 03-11 vs 03-18 | **Looks like #3 but is not.** Two occurrences of one series. A naive dedupe rule collapses these and loses a real meeting. |
| 5 | `CRM-1002` ↔ `CAL-A2` | CRM: `In-Person` @ "NYC Office - 30th Floor". Calendar: `Zoom - https://zoom.us/j/...` | The conflict named in the brief. Irreconcilable — both must stay visible. |
| 6 | `CRM-1009` ↔ `CAL-A10` | CRM `Cancelled` (notes: "rescheduled to 3/26"); calendar `confirmed` | **Highest stakes.** Stale calendar. Someone could attend a cancelled meeting. |
| 7 | `CRM-1016` ↔ `CAL-A17` | Same client, same day, same platform — `13:00` vs `15:00` | Not rounding. Match-and-flag, or two separate meetings? |
| 8 | `CRM-1007` | `meeting_time: null`, `location: null` | `CAL-A8` supplies both → **gap fill, not conflict.** |
| 9 | `CAL-A11` | `end_time: "2025-03-14T20:00"` (no seconds), `attendees: []`, null location and description | Malformed *and* an orphan. Must not crash the parser. |
| 10 | `CAL-A16`, `CAL-A20` | `"raj.patel[at]atlasvc.com"`, `"external-guests"` | Break any naive email→person resolution. |
| 11 | `CRM-1011` ↔ `CAL-A12` | "Annual Allocation Review" vs "Horizon Wealth - Year-End Review" | Share almost no tokens. **Proves title similarity cannot be load-bearing.** |
| 12 | `CRM-1013` ↔ `CAL-A14` | CRM owner Sarah Chen; calendar organizer Priya Sharma | Owner conflict. Also means organizer is a weak matching signal. |
| 13 | `CRM-1006/1009/1013/1019` | `client_name: null` | **Legitimately** null (internal meetings). Must not be reported as malformed. |
| 14 | — | CRM-only: `1003`, `1010`, `1020`. Calendar-only: `A3`, `A11`, `A18`, `A19` | Coverage gaps. "Which system is missing this?" is a first-class question. |

### 2.3 Expected output, derived by hand before coding

**17 matched pairs · 3 CRM-only · 4 calendar-only · 1 intra-source duplicate ·
24 unified meetings.**

This prediction was written down *first* and then used as the test oracle —
`TestMatching.EXPECTED_PAIRS` in `backend/tests/test_reconciliation.py` is the
hand-derived pairing, not a snapshot of whatever the code happened to produce.

---

## 3. Architecture

Four stages, each independently testable, with intermediate artefacts kept
rather than discarded:

```
  crm_events.json ─┐
                   ├─►[1] INGEST ─►[2] DEDUPE ─►[3] MATCH ─►[4] MERGE ─► API ─► UI
calendar_events.json┘   normalize     within-      cross-      field-level
                        + quarantine   source       source      provenance
                                       (A5/A6)      scoring     + conflicts
```

Stage order is not arbitrary. **Dedupe must precede match** (landmine #3), and
the dedupe rule must be recurrence-safe (landmine #4).

Keeping each stage's by-products — the warnings, the duplicate report, the
per-signal match breakdown — is what lets the UI explain itself instead of just
asserting results.

### Why a weighted score rather than rules or ML

Hand-written rules would need a branch per landmine and would not generalize.
ML is unjustifiable on 42 records and unauditable besides. A weighted sum of
named signals is auditable: every pair keeps the breakdown that produced its
number, and that breakdown is served to the UI.

| Signal | Weight | Rationale |
|---|---|---|
| Participants | 0.35 | Strongest. Attendee emails resolve to names that compare directly against `client_name`. |
| Time proximity | 0.28 | Strong, but #7 shows it cannot be exact-match. |
| Title similarity | 0.17 | Weak alone (#11); useful with a company-name boost. |
| Owner / organizer | 0.10 | Deliberately weak — #12 shows organizer ≠ owner. |
| Location agreement | 0.10 | Needed to carry internal meetings, which have no client to match on. |

Signals return *not applicable* when a field is missing on either side, and
weights are renormalized over the applicable ones — a null location dilutes the
score rather than scoring zero against it.

### Three tiers, not two

- **≥ 0.70** → merge automatically
- **0.45 – 0.70** → surface in a review queue; do not decide
- **< 0.45** → treat as unrelated

The middle band is the honest answer to "we intentionally didn't tell you how
to handle this". *(On this dataset the band turns out to be empty — every pair
scored decisively. That is reported as-is rather than tuned to look busier.)*

---

## 4. Conflict model

A conflict is a first-class object, not a UI afterthought. Every field on a
unified meeting is a `FieldValue` carrying the chosen value, its source, and
**both candidates**:

```json
"location": {
  "value": "Zoom - https://zoom.us/j/98765432100",
  "chosen_from": "calendar",
  "conflict": true,
  "severity": "high",
  "candidates": [
    { "source": "crm",      "record_id": "CRM-1002", "value": "NYC Office - 30th Floor" },
    { "source": "calendar", "record_id": "CAL-A2",   "value": "Zoom - https://zoom.us/j/98765432100" }
  ]
}
```

### Severity, by consequence rather than by field

| Severity | Meaning | Example |
|---|---|---|
| `critical` | Someone acts on false information | Cancelled in CRM, confirmed in calendar (#6) |
| `high` | Someone goes to the wrong place or time | Zoom vs In-Person (#5); 2h drift (#7) |
| `medium` | Needs confirmation, unlikely to cause harm | 30–60 min drift; owner mismatch (#12) |
| `low` | Cosmetic | Differing titles; `completed` vs `confirmed` |

### Avoiding false alarms

Crying wolf is a real failure mode. Three rules suppress noise:

- **Missing ≠ conflicting.** One side null is a gap fill (#8).
- **More specific ≠ different.** "HQ - Conference Room B" and "Conference
  Room B" agree. Comparison uses token containment, not equality.
- **Lifecycle ≠ contradiction.** CRM `completed` vs calendar `confirmed` is not
  a disagreement; the calendar has no completion state. Flagged `low`.

---

## 5. Open questions resolved during the session

**Q: `CRM-1016` 13:00 vs `CAL-A17` 15:00 — match or not?**
Match and flag. A two-hour drift on the same client, same day, same platform is
exactly what a sync service exists to catch. Leaving them as two meetings would
silently double-count and hide the drift.

**Q: `CAL-A4`'s timezone.**
2025-03-13 falls after US DST began (Mar 9), so `19:00Z` is **15:00 EDT**, not
14:00 EST. Assuming EST would make it line up exactly with the CRM — which is
probably what the fixture author intended — but would be wrong about the
calendar. Chose correctness and let the resulting 1-hour delta surface as a
`medium` conflict. Recorded as ADR-003.

**Q: Which source wins on `location`/`modality` for `CRM-1002`?**
The calendar. The CRM's "In-Person" is a dropdown value; the calendar holds an
actual Zoom join URL. A real artefact beats a categorisation. Recorded as
ADR-006.

---

## 6. What was deliberately not built

- **Auth / multi-tenancy** — no requirement, and it would bury the reconciliation logic.
- **A database** — the sources are static files; `POST /api/sync` is the seam where a real scheduler would go.
- **Fuzzy-matching libraries** (`rapidfuzz`, `dedupe`) — matching behaviour is the thing under review, so it is stdlib and readable.
- **Frontend tests** — explicitly de-prioritised by the brief; backend tests pin the logic that actually makes decisions.
- **Write-back to source systems** — out of scope, and the interesting half of that problem is conflict *resolution* policy, which this service deliberately leaves to a human.
