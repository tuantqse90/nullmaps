"""VN-specific query normalization + a trigram helper for the geocoder.

Pure functions (no DB, no FastAPI) so they are unit-testable and shared by the
service (app/main.py) and the index builder (indexops.py / importer.py).
Inputs are expected to be already diacritic-folded (see app.main.fold)."""
from __future__ import annotations

import re

_HOUSENO = re.compile(r"^\s*\d+\s*[a-z]?\s+")        # "123 ", "12a "
_STREET_PREFIX = re.compile(r"^(?:duong|d\.)\s+")     # leading street-type word
_Q = re.compile(r"\bq\.?\s*(\d+)\b")                  # q1 / q.1 / q 1 -> quan N
_P = re.compile(r"\bp\.?\s*(\d+)\b")                  # p3 -> phuong N
_TP = re.compile(r"\btp\.?\b")                        # tp / tp. -> thanh pho
_TX = re.compile(r"\btx\.?\b")                        # tx -> thi xa


def normalize_query(folded: str) -> str:
    """Normalize an already-folded VN query. Conservative: only expands q/p when
    followed by a digit, so plain tokens inside a name are left untouched."""
    s = _HOUSENO.sub("", folded)
    s = _STREET_PREFIX.sub("", s)
    s = _Q.sub(r"quan \1", s)
    s = _P.sub(r"phuong \1", s)
    s = _TP.sub("thanh pho", s)
    s = _TX.sub("thi xa", s)
    return s.strip()


def trigrams(s: str) -> set[str]:
    """pg_trgm-style trigram set: pad with spaces, return the set of 3-grams."""
    s = "  " + s + " "
    return {s[i:i + 3] for i in range(len(s) - 2)}
