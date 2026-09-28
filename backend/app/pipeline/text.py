"""String normalization and similarity helpers.

Deliberately stdlib-only. A fuzzy-matching library would be faster to write but
harder for a reviewer to audit, and matching behaviour is the thing being
reviewed here.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# Words that carry no signal when comparing meeting titles.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "for", "with", "to", "re",
    "meeting", "call", "session", "discussion", "sync", "w",
}

# Site prefixes that appear in one source but not the other
# ("HQ - Conference Room B" vs "Conference Room B").
_LOCATION_PREFIXES = re.compile(
    r"^(hq|headquarters|nyc office|ny office|dc office|boston office|office)\s*[-–:]\s*",
    re.IGNORECASE,
)

_VIRTUAL_HINTS = (
    "zoom", "teams", "microsoft teams", "google meet", "meet.google",
    "webex", "virtual", "http://", "https://", "dial-in", "conference bridge",
)


def squash(value: str | None) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    if not value:
        return ""
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", value.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def tokens(value: str | None, drop_stopwords: bool = True) -> set[str]:
    words = squash(value).split()
    if drop_stopwords:
        words = [w for w in words if w not in _STOPWORDS]
    return set(words)


def normalize_person_name(value: str | None) -> str:
    """'Kevin O'Brien' and 'kevin.obrien' both collapse to 'kevin obrien'."""
    if not value:
        return ""
    cleaned = value.lower().replace("'", "").replace("`", "").replace("’", "")
    cleaned = re.sub(r"[._\-+]+", " ", cleaned)
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def company_key(value: str | None) -> str:
    """'Meridian Capital' -> 'meridiancapital'.

    Suffixes are kept, not stripped, because we compare company names against
    email domains by common prefix: 'meridiancap.com' -> 'meridiancap' is a
    prefix of 'meridiancapital'. Dropping 'capital' would destroy exactly the
    signal we depend on.
    """
    return squash(value).replace(" ", "")


def common_prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def similarity(a: str | None, b: str | None) -> float:
    """Max of token-Jaccard and character-sequence ratio, 0..1.

    Two measures because meeting titles fail in two different ways:
    'QBR - Q1 Wrap' vs 'Quarterly Business Review' shares no tokens, while
    'ESG Compliance Discussion' vs 'ESG + Compliance Review' shares most.
    """
    if not a or not b:
        return 0.0
    ta, tb = tokens(a), tokens(b)
    jaccard = len(ta & tb) / len(ta | tb) if (ta and tb) else 0.0
    ratio = SequenceMatcher(None, squash(a), squash(b)).ratio()
    return max(jaccard, ratio)


def containment(a: str | None, b: str | None) -> float:
    """Fraction of the smaller token set contained in the larger."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def normalize_location(value: str | None) -> str:
    """Strip site prefixes and URLs so room names can be compared directly."""
    if not value:
        return ""
    cleaned = value.strip()
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = _LOCATION_PREFIXES.sub("", cleaned)
    cleaned = re.sub(r"^virtual\s*[-–:]\s*", "", cleaned, flags=re.IGNORECASE)
    return squash(cleaned)


def looks_virtual(location: str | None) -> bool:
    if not location:
        return False
    low = location.lower()
    return any(hint in low for hint in _VIRTUAL_HINTS)


def title_case_name(value: str) -> str:
    parts = [p for p in normalize_person_name(value).split() if p]
    return " ".join(p.capitalize() for p in parts)
