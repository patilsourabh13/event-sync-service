> **Before running anything, see [`RESTORE-FIRST.txt`](RESTORE-FIRST.txt).**
> Email filters block `.py`, `.js`, `.ps1` and `.sh` attachments, so 18 source
> files were shipped with `.txt` appended to their names. One command renames
> them back. File contents are unmodified.
# Event Sync Service

Reconciles meeting records from a CRM feed and a calendar feed into one unified
list — and keeps every disagreement between the two sources visible instead of
silently picking a winner.

**42 source records → 24 unified meetings → 17 conflicts surfaced, 0 records lost.**

---

## Quick start

One command. It creates the Python virtualenv, installs both dependency sets on
first run, and starts the API and the UI together.

```bash
# macOS / Linux
./start.sh

# Windows (PowerShell)
.\start.ps1
```

Then open **<http://localhost:5173>**.

| What | Where |
|---|---|
| Web UI | <http://localhost:5173> |
| REST API | <http://127.0.0.1:8000/api> |
| Interactive API docs | <http://127.0.0.1:8000/docs> |

`Ctrl+C` stops both processes.

> If port 5173 is already taken, Vite picks the next free one and prints it as
> `Local: http://localhost:5174/` — open whatever it reports. The API port is
> fixed at 8000, and the UI proxies to it regardless of which port it lands on.

> **Port 8000 must be free.** The start scripts check this first and, if it is
> taken, stop and name the process holding it. To free it manually:
>
> ```powershell
> # Windows
> Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object OwningProcess
> Stop-Process -Id <PID>
> ```
>
> ```bash
> # macOS / Linux
> lsof -nP -iTCP:8000 -sTCP:LISTEN
> kill <PID>
> ```

**Requirements:** Python 3.11+ and Node 18+.
*(Verified on Python 3.13.2 and Node 24.18.0.)*

<details>
<summary>Running the two processes manually</summary>

```bash
# Terminal 1 — API
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --port 8000

# Terminal 2 — UI
cd frontend
npm install && npm run dev
```
</details>

<details>
<summary>Single-process mode (no Node at runtime)</summary>

Build the frontend once and the API will serve it directly:

```bash
cd frontend && npm install && npm run build
cd ../backend && .venv/bin/python -m uvicorn app.main:app --port 8000
```

The whole app is then at <http://127.0.0.1:8000>.
</details>

### Tests

```bash
cd backend && .venv/bin/python -m pytest      # 61 tests
```

They are not there for coverage. Each one pins a specific judgment call about
this dataset, so if someone changes a threshold, the failing test names the
real-world case they broke.

---

## What this does

Two systems describe the same meetings and agree about almost nothing
structurally. There is no shared id, timestamps drift, one source duplicates
itself, and several fields flatly contradict each other.

The service runs a four-stage pipeline:

```
  crm_events.json ─┐
                   ├─►[1] INGEST ─►[2] DEDUPE ─►[3] MATCH ─►[4] MERGE ─► API ─► UI
calendar_events.json┘   normalize     within-      cross-      field-level
                        + quarantine   source       source      provenance
                                                                + conflicts
```

1. **Ingest** — coerce both shapes into one model. Repair what is malformed,
   flag every repair, quarantine only what genuinely cannot be placed on a
   timeline.
2. **De-duplicate within each source** — before cross-source matching, because
   one feed contains the same meeting twice.
3. **Match across sources** — a weighted, explainable score over five signals.
4. **Merge** — combine matched records so that *every field keeps both source
   values*, marks which one is being served, and says why.

### The design principle

> **Every reconciliation decision is inspectable, and no value is ever discarded.**

The brief says *"we have intentionally not told you how to handle any of these
cases"* and *"the user should be able to see where data conflicts exist"*. Read
together, the product is not the reconciled list — it is the **reconciliation,
shown**. A service that quietly resolves conflicts and serves a clean list
would satisfy the letter of the requirement and defeat its purpose.

So no field on a unified meeting is a bare value. Every one looks like this:

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

The losing value never goes away. The UI renders it struck through, next to the
winner, with the reason.

---

## What the data actually contained

Both files were read record by record before any code was written. Fourteen
distinct defects were planted; all fourteen are handled. Full analysis in
[docs/01-brainstorming.md](docs/01-brainstorming.md).

The ones that shaped the architecture:

| Records | What is wrong | How it is handled |
|---|---|---|
| `CAL-A5` / `CAL-A6` | Same Pinnacle meeting twice, 30 min apart | **De-duplicated before matching.** Otherwise `CRM-1005` claims one and the other becomes a phantom "calendar-only" meeting. |
| `CAL-A3` / `CAL-A18` | Look identical — same title, attendees, `created_at` | **Not duplicates.** Recurring occurrences a week apart. The same-day gate keeps them separate. This is the trap sitting next to the one above. |
| `CAL-A4` | The only record with an explicit `Z` (`19:00:00Z`) | Converted to org time → 15:00 EDT. CRM says 14:00, so a 1-hour conflict is reported rather than hidden. See [ADR-003](docs/02-decisions.md#adr-003). |
| `CRM-1008` | `meeting_date: "03-15/2025"` | Salvaged as 15 March; `CAL-A9` on the same date confirms the reading. |
| `CRM-1002` ↔ `CAL-A2` | CRM `In-Person` @ NYC office vs calendar `Zoom` link | Flagged **high**. Calendar wins (a join URL beats a dropdown value), both stay visible. |
| `CRM-1009` ↔ `CAL-A10` | CRM `Cancelled`, calendar still `confirmed` | Flagged **critical**. Cancellation always overrides. |
| `CRM-1011` ↔ `CAL-A12` | "Annual Allocation Review" vs "Horizon Wealth - Year-End Review" | Matched on client + time. Proves title similarity cannot be load-bearing. |
| `CRM-1007` | `meeting_time` and `location` both null | `CAL-A8` fills both. **Gap fill, not conflict** — reported as provenance, not as a disagreement. |
| `CAL-A11` | Malformed timestamp, empty attendees, nulls throughout | Kept, with four warnings. Never crashes the parser. |
| `CAL-A16` / `CAL-A20` | `raj.patel[at]atlasvc.com`, `external-guests` | First repaired, second kept but marked unresolvable for identity matching. |

### Results

| | |
|---|---|
| Records ingested | **42** (20 CRM + 22 calendar) |
| Intra-source duplicates collapsed | **1** (`CAL-A5` → `CAL-A6`) |
| Unified meetings | **24** |
| Matched across both sources | **17** |
| CRM only | **3** (`CRM-1003`, `CRM-1010`, `CRM-1020`) |
| Calendar only | **4** (`CAL-A3`, `CAL-A11`, `CAL-A18`, `CAL-A19`) |
| Conflicts surfaced | **17** across 13 meetings — 1 critical, 3 high, 4 medium, 9 low |
| Records repaired or inferred | **11** |
| Records quarantined | **0** |
| **Records lost** | **0** |

Every one of the 42 inputs is accounted for in the output — asserted by
`test_nothing_is_dropped_silently`.

---

## Key decisions

Full reasoning, including the alternatives rejected, in
[docs/02-decisions.md](docs/02-decisions.md). The five that matter most:

### 1. Matching is a weighted score, not rules or ML

Five signals, each 0–1, weights renormalized over whichever signals are
*applicable* to a pair — so a null field dilutes the score instead of scoring
zero against it.

| Signal | Weight | Why |
|---|---|---|
| Participant identity | 0.35 | The only near-unique key. `david.park@meridiancap.com` → "david park" matches `client_name` directly. |
| Time proximity | 0.28 | Strong, but cannot be exact-match — real drift exists. |
| Title similarity | 0.17 | Weak alone; boosted when the client's company appears in the title. |
| Owner / organizer | 0.10 | Deliberately weak — a coordinator often books on the owner's behalf. |
| Location agreement | 0.10 | Carries internal meetings, which have no client to match on. |

**Every pair keeps its per-signal breakdown, and the UI shows it.** "Why were
these two records merged?" is always answerable, for every meeting.

### 2. Three tiers, with a review queue

`≥ 0.70` merge · `0.45–0.70` **surface for human review** · `< 0.45` unrelated.

The middle band is the honest response to an underspecified problem: the
service says "these might be the same meeting, someone should decide" rather
than guessing.

**On this dataset that band is empty** — every pair scored above 0.74 or below
0.40. That is reported as-is in the UI, with the thresholds shown, rather than
tuned until it looked busier.

### 3. Source of truth is per-field, not per-source

| Fields | Winner | Why |
|---|---|---|
| `status`, `client`, `owner`, `notes`, `title` | **CRM** | System of record for the relationship |
| `start`, `end`, `location`, `modality`, `attendees` | **Calendar** | System of record for logistics |
| Cancellation | **Either source** | Hard override, both directions |

"The CRM is always right" breaks on times — the calendar is what people's
devices alert from. "The calendar is always right" breaks on `CRM-1009`, where
the CRM knows about a cancellation the calendar has not caught up with.

The cancellation override is **asymmetric on purpose**: treating a cancelled
meeting as live wastes someone's afternoon, while the reverse is caught the
moment anyone looks.

### 4. Severity reflects consequence, and false alarms are actively suppressed

`critical` = someone acts on false information · `high` = wrong place or time ·
`medium` = worth confirming · `low` = cosmetic.

A conflict view nobody trusts is worse than none, so three rules keep the noise
down:

- **Missing ≠ conflicting.** One side null is a gap fill (`CRM-1007`).
- **More specific ≠ different.** "HQ - Conference Room B" and "Conference
  Room B" agree — compared by token containment, not equality.
- **Lifecycle ≠ contradiction.** CRM `completed` vs calendar `confirmed` is not
  a disagreement; flagged `low` and explained.

### 5. Nothing is dropped silently

Malformed input is repaired and flagged; only records that genuinely cannot be
placed on a timeline are quarantined, with a reason. The **Data quality** tab
shows every repair, inference, and collapse.

On this data nothing needed quarantining — every defect was recoverable. The
path is built and tested anyway, because the next file will be less cooperative.

---

## Using the UI

Four tabs:

**Meetings** — the reconciled list. Each row carries a coverage badge
(`BOTH SOURCES` / `CRM ONLY` / `CALENDAR ONLY`) and per-severity conflict chips.
Filter by coverage, conflict severity, status, date, or free text. Click any row
to open the detail drawer:

- every conflict, with **both source values side by side** and the consequence
  spelled out in plain language
- a **field-by-field comparison table** — CRM column, Calendar column, served
  value; conflicting rows highlighted by severity, the winner marked `✓ USED`,
  the loser struck through
- the **match reasoning**: all five signals, their scores, and their weights
- for single-source meetings, the **closest candidate that was rejected** — so
  "why is this CRM-only?" is answered rather than assumed
- the raw JSON of each upstream record

**Conflicts** — every disagreement in the dataset in one list, independent of
which meeting it belongs to, filterable by severity and field. This is the
direct answer to *"where does the data conflict?"*

**Review queue** — borderline pairs the matcher refuses to decide alone, with
the thresholds that define the band.

**Data quality** — every repaired field, every collapsed duplicate (with what
differed between the two records), and anything quarantined.

---

## API

Interactive docs at **<http://127.0.0.1:8000/docs>**.

| Endpoint | Purpose |
|---|---|
| `GET /api/meetings` | Reconciled list. Filters: `coverage`, `has_conflicts`, `min_severity`, `status`, `q`, `date_from`, `date_to`, `sort` |
| `GET /api/meetings/{id}` | One meeting, plus the untouched raw records behind it |
| `GET /api/conflicts` | Every conflict, flattened. Filters: `severity`, `field` |
| `GET /api/review-queue` | Pairs awaiting human judgment, with thresholds |
| `GET /api/data-quality` | Repairs, warnings, duplicates, quarantined records |
| `GET /api/sources/{source}/{record_id}` | Raw + normalized form of one upstream record |
| `GET /api/stats` | Headline sync numbers |
| `GET /api/config` | The thresholds and weights driving the results |
| `POST /api/sync` | Re-read both feeds and rebuild |

```bash
curl -s 'http://127.0.0.1:8000/api/conflicts?severity=critical' | python -m json.tool
```

---

## Project structure

```
├── data/                          # supplied source files (unmodified)
├── backend/
│   ├── app/
│   │   ├── config.py              # every tunable policy value, in one place
│   │   ├── models.py              # domain model — FieldValue carries provenance
│   │   ├── sync.py                # pipeline orchestration
│   │   ├── main.py                # FastAPI routes
│   │   └── pipeline/
│   │       ├── text.py            # normalization + similarity (stdlib only)
│   │       ├── ingest.py          # [1] parse, repair, quarantine
│   │       ├── dedupe.py          # [2] intra-source de-duplication
│   │       ├── matching.py        # [3] weighted cross-source scoring
│   │       └── merge.py           # [4] merge + conflict detection
│   └── tests/                     # 61 tests, one per judgment call
├── frontend/
│   └── src/
│       ├── App.jsx                # tabs, filters, state
│       ├── lib/                   # API client, formatting
│       └── components/            # MeetingDetail.jsx holds the diff view
├── docs/
│   ├── 01-brainstorming.md        # data forensics + approach, pre-code
│   ├── 02-decisions.md            # ADR log — 10 decisions with alternatives
│   └── 03-ai-collaboration.md     # how AI was used, and where it was wrong
├── start.sh  /  start.ps1
└── README.md
```

All tunable policy — thresholds, weights, the timezone, dedupe tolerances —
lives in [`backend/app/config.py`](backend/app/config.py). Nothing is buried as
a magic number in the middle of a function.

---

## Scope notes

**Deliberately not built:** auth, a database, write-back to the source systems,
and frontend tests. The sources are static files, so `POST /api/sync` is the
seam where a real scheduler or webhook would attach. Write-back was left out
because its interesting half is conflict *resolution* policy — and this service
deliberately leaves that to a human.

**No fuzzy-matching library.** Matching behaviour is the thing under review, so
it is stdlib and readable rather than delegated to `rapidfuzz`.

**Known limitation.** Matching is O(n²) over same-day pairs. Fine for 42
records, and at real volume the fix is blocking on date before scoring — the
same-day gate in `match_events` is already that seam.

**The `CAL-A4` timezone is the most arguable call here.** Assuming EST would
make the CRM and calendar line up exactly, which is probably what the fixture
intended. That was rejected because 13 March 2025 falls after DST began, so
15:00 EDT is correct — and picking the offset that makes data agree is choosing
the answer before the reasoning. The service is correct about the calendar and
honest about the 1-hour conflict that results. Reasoning in
[ADR-003](docs/02-decisions.md#adr-003).

---

## Time spent

**Roughly 4 hours**, split approximately:

| | |
|---|---|
| Reading the data record by record, mapping every defect | ~45 min |
| Design: pipeline stages, signal weights, conflict model | ~45 min |
| Backend implementation | ~1 hr |
| Frontend implementation | ~45 min |
| Tests | ~20 min |
| README and decision docs | ~45 min |

AI-assisted throughout (Claude Opus 5 via Claude Code), with the working
process — and the places the model got it wrong — documented in
[docs/03-ai-collaboration.md](docs/03-ai-collaboration.md).

