# AI collaboration notes

The brief encourages AI assistance and asks for transparency about it. This
records how it was actually used, including where it was wrong.

**Tool:** Claude Opus 5 via Claude Code, working directly in the repository.

---

## How the session was structured

**1. Data forensics before any design.** The first thing asked of the model was
not "build a sync service" but "read both files completely and tell me what is
deliberately broken." Both JSON files were read end to end and all 42 records
cross-referenced manually. That produced the fourteen-item landmine table in
[01-brainstorming.md](01-brainstorming.md) — including the two that matter
most and are easy to miss:

- `CAL-A5`/`CAL-A6` are a genuine intra-source duplicate, **and**
- `CAL-A3`/`CAL-A18` look identical but are recurring occurrences that must
  *not* be collapsed.

Designing the dedupe rule against only the first would have silently deleted a
real meeting. Finding both before writing code is what made the same-day gate
an obvious requirement rather than a later bug fix.

**2. Expected output derived by hand, then used as the test oracle.**
"17 matched pairs, 3 CRM-only, 4 calendar-only, 24 unified" was worked out on
paper from the raw records *before* the matcher existed.
`TestMatching.EXPECTED_PAIRS` is that hand-derived list. This matters: had the
expectations been written after seeing the output, the tests would assert that
the code does what it does, which is worth nothing.

**3. Signal weights validated arithmetically before implementation.** Candidate
weights were hand-multiplied through the six hardest pairs (`CRM-1013↔A14`,
`CRM-1017↔A20`, `CRM-1016↔A17`, `CRM-1011↔A12`, `CRM-1007↔A8`, and the
near-miss `CRM-1003↔A11`) to check the thresholds separated them correctly.
Two problems surfaced on paper and were fixed before any code was written:

- Internal meetings scored too low to clear 0.70, because with no client to
  match on they had only three usable signals. Fixed by adding location
  agreement as a fifth signal — the one thing internal meetings reliably share.
- Location comparison originally stripped site prefixes, which made
  "NYC Office" and "NYC Office - 12th Floor" look *dissimilar*. Switching to
  token containment fixed it. This would have been a confusing runtime bug.

**4. Incremental verification.** Each stage was run against the real data
immediately after being written, rather than building everything and debugging
at the end. Ingest output was inspected before dedupe was written; dedupe
output before matching; and so on.

**5. End-to-end UI verification.** The frontend was not assumed to work because
it compiled. The production bundle was loaded in jsdom against the live API and
24 assertions were run over the rendered DOM — meeting counts, badge counts,
the conflict diff table, the winner/loser markers, the signal breakdown, and
each tab. That harness was throwaway and is not in the repository, per the
decision not to ship frontend tests.

---

## Where the model was wrong, and how it was caught

**Location matching, caught by arithmetic.** The first design normalized
locations by stripping site prefixes before comparing. Hand-computing
`CRM-1011 ↔ CAL-A12` showed this turned "NYC Office" into `"nyc office"` but
"NYC Office - 12th Floor" into `"12th floor"` — a *zero* similarity score for
two locations that plainly agree. Caught on paper; the fix was containment over
tokens.

**A `company_key` helper that did nothing.** An early version applied a regex
whose replacement returned the matched text unchanged — an elaborate no-op.
Spotted on review of the written file.

**Two tooling mistakes worth recording**, because they are the kind of thing
that corrupts a repo quietly:

- A `perl -pi` in-place edit intended to rewrite one function silently deleted
  it instead, due to shell quoting. Caught because the next command's `grep`
  returned nothing. The file was rewritten wholesale and in-place regex edits
  were abandoned for the rest of the session.
- The jsdom harness initially reported a spurious failure ("Could not reach the
  API") because it read `document.body.textContent`, which included the source
  of the injected `<script>` tag — so it matched error strings present in the
  *bundle* rather than on the page. The check was wrong, not the app. Fixed by
  reading from the mounted `#root` only.

Both are recorded because "the tool reported success" and "the thing works" are
different claims, and the gap between them is where this sort of mistake lives.

---

## What was *not* delegated

The decisions in [02-decisions.md](02-decisions.md) were made deliberately, not
accepted as defaults. The two that took the most thought:

**ADR-003, the `CAL-A4` timezone.** The model's first instinct was to treat the
naive timestamps as EST so that `19:00Z` would resolve to 14:00 and match the
CRM exactly. That was rejected: 13 March 2025 is after DST began, so the
correct conversion is 15:00 EDT. Choosing the offset that makes the data agree
is picking the answer first and the reasoning afterwards. The service is now
correct about the calendar and honest about the resulting 1-hour conflict.

**ADR-006, who wins on `CRM-1002`.** Arguable in both directions. The calendar
was chosen because a working Zoom URL is a harder artefact than a dropdown
value — but the losing value stays on screen, because the honest answer is that
the service cannot actually know.

---

## Assessment of the collaboration

Genuinely accelerated: exhaustive cross-referencing of 42 records, boilerplate
(Pydantic models, React components, CSS), and drafting these documents.

Required active supervision: anything where the model's default was the
*convenient* answer rather than the correct one — the timezone call being the
clearest example. The useful pattern was forcing predictions onto paper before
implementation, so that the code had something independent to be checked
against rather than being its own specification.
