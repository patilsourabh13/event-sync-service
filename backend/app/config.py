"""Tunable policy for the reconciliation pipeline.

Everything here is a *judgment call*, not a fact about the data. Each value is
documented in docs/02-decisions.md so a reviewer can disagree with a number
without having to read the code to find it.
"""

from pathlib import Path

# --- Paths ----------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
CRM_FILE = DATA_DIR / "crm_events.json"
CALENDAR_FILE = DATA_DIR / "calendar_events.json"

# --- Time -----------------------------------------------------------------
# The calendar feed mixes naive local timestamps with one explicit-UTC stamp
# (CAL-A4). We assume the firm operates out of US/Eastern and that every naive
# timestamp is already org-local. See ADR-003.
ORG_TIMEZONE = "America/New_York"

# Internal staff are identified by email domain.
INTERNAL_DOMAINS = {"firma.com"}

# --- Matching -------------------------------------------------------------
# Signal weights. Renormalized over whichever signals are *applicable* to a
# given pair, so a missing field dilutes nothing (see ADR-005).
SIGNAL_WEIGHTS = {
    "participants": 0.35,
    "time": 0.28,
    "title": 0.17,
    "owner": 0.10,
    "location": 0.10,
}

AUTO_MATCH_THRESHOLD = 0.70   # >= this -> merged into one meeting
REVIEW_THRESHOLD = 0.45       # >= this but < auto -> surfaced in review queue
                              # <  this -> treated as unrelated

# A pair on different calendar days is never a match, whatever else agrees.
SAME_DAY_HARD_GATE = True

# --- Intra-source de-duplication -----------------------------------------
DEDUPE_MAX_MINUTES_APART = 60
DEDUPE_MIN_PARTICIPANT_OVERLAP = 0.5
DEDUPE_MIN_TITLE_SIMILARITY = 0.35

# --- Conflict severity ----------------------------------------------------
TIME_CONFLICT_MEDIUM_MINUTES = 15   # > this -> medium
TIME_CONFLICT_HIGH_MINUTES = 60     # > this -> high
