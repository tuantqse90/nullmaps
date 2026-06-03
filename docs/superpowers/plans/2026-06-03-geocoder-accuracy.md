# Geocoder Accuracy (VN) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lift VN typeahead/geocode hit-rate and reverse-geocode trustworthiness in the lightweight SQLite geocoder — query normalization, hand-built trigram-similarity typo fallback, admin-boundary indexing, reverse kind-preference + distance cap, and smoother prominence + street de-duplication.

**Architecture:** Pure VN helpers (`app/vnorm.py`) and pure index ops (`indexops.py`) are split out so the index-build logic is unit-testable without `osmium`. `app/main.py` wires them into search/reverse; `importer.py` calls them during the (osmium) build pass. Tests use a seeded in-memory SQLite fixture (no osmium).

**Tech Stack:** Python 3.12, SQLite (FTS5 unicode61 + R*Tree + a hand-built `trgm` table), pyosmium 3.7, FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-06-03-geocoder-accuracy-design.md`

**Branch:** `feat/geocoder-accuracy` (already created)

**Reindex note:** items touching `importer.py`/`indexops.py` only take effect after a box reindex (`make geo-index` or `infra/refresh.sh`, now rollback-protected). The query-path items take effect on service restart.

---

## File Structure

- **Create** `services/geocoder/app/vnorm.py` — `normalize_query()` + `trigrams()`; pure, no DB/FastAPI.
- **Create** `services/geocoder/indexops.py` — `build_trigrams(con)`, `merge_streets(con)`; pure SQL on a connection (imports `trigrams` from `app.vnorm`), no osmium.
- **Modify** `services/geocoder/app/main.py` — wire `vnorm`, add `_trigram_fallback`, harden `reverse()`, add `boundary` to `KIND_RANK`.
- **Modify** `services/geocoder/importer.py` — `area()` admin handler, log-scaled `importance()`, call `indexops` in `build()`.
- **Create** `services/geocoder/tests/test_vnorm.py`, `tests/test_indexops.py`, `tests/test_search_fixture.py`.
- **Modify** `services/geocoder/README.md`, `docs/runbook-ops.md`.

---

## Task 1: `app/vnorm.py` — normalize_query + trigrams

**Files:**
- Create: `services/geocoder/app/vnorm.py`
- Test: `services/geocoder/tests/test_vnorm.py`

- [ ] **Step 1: Write the failing tests**

Create `services/geocoder/tests/test_vnorm.py`:

```python
"""Pure tests for VN query normalization + trigram helper (no DB)."""
from app.vnorm import normalize_query, trigrams


def test_strip_leading_house_number():
    assert normalize_query("123 nguyen hue") == "nguyen hue"
    assert normalize_query("12a le loi") == "le loi"


def test_expand_quan_phuong_with_digit():
    assert normalize_query("q1") == "quan 1"
    assert normalize_query("q.1 nguyen hue") == "quan 1 nguyen hue"
    assert normalize_query("p3") == "phuong 3"


def test_does_not_touch_bare_letters_in_names():
    assert normalize_query("phu nhuan") == "phu nhuan"   # bare 'p', no digit
    assert normalize_query("quan an ngon") == "quan an ngon"


def test_city_markers_and_street_prefix():
    assert normalize_query("tp hcm") == "thanh pho hcm"
    assert normalize_query("tx di an") == "thi xa di an"
    assert normalize_query("duong le loi") == "le loi"
    assert normalize_query("d. le loi") == "le loi"


def test_trigrams_overlap_catches_typo():
    a, b = trigrams("nguyn hue"), trigrams("nguyen hue")
    shared = len(a & b)
    jac = shared / len(a | b)
    assert jac >= 0.5            # internal typo still highly similar
    assert not (trigrams("q1") & trigrams("nguyen hue"))  # unrelated -> no overlap
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/geocoder && PYTHONPATH=. python3 -m pytest tests/test_vnorm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.vnorm'`

- [ ] **Step 3: Create `app/vnorm.py`**

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/geocoder && PYTHONPATH=. python3 -m pytest tests/test_vnorm.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add services/geocoder/app/vnorm.py services/geocoder/tests/test_vnorm.py
git commit -m "feat(geocoder): VN query normalization + trigram helper (vnorm)"
```

---

## Task 2: Wire normalization into `main.py` search

**Files:**
- Modify: `services/geocoder/app/main.py` (`fts_match` ~line 41, `search` ~line 52)
- Test: `services/geocoder/tests/test_vnorm.py` (one extra assertion) + existing `tests/test_fold.py`

- [ ] **Step 1: Add a failing test for normalized fts_match**

Append to `services/geocoder/tests/test_vnorm.py`:

```python
def test_fts_match_uses_normalization():
    from app.main import fts_match
    # q1 expands to "quan 1"; existing behavior for plain queries is unchanged
    assert fts_match("q1") == '"quan"* "1"*'
    assert fts_match("ben thanh") == '"ben"* "thanh"*'
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/geocoder && PYTHONPATH=. python3 -m pytest tests/test_vnorm.py::test_fts_match_uses_normalization -v`
Expected: FAIL — `fts_match("q1")` currently returns `'"q1"*'`

- [ ] **Step 3: Wire `vnorm` into `main.py`**

In `services/geocoder/app/main.py`, add the import after the existing imports (below `from fastapi import FastAPI, Query`):

```python
from app.vnorm import normalize_query, trigrams
```

Replace `fts_match` (currently):

```python
def fts_match(q: str) -> str:
    """Build a prefix FTS5 query: each token becomes token* (typeahead)."""
    toks = [t for t in fold(q).replace('"', " ").split() if t]
    return " ".join(f'"{t}"*' for t in toks)
```

with:

```python
def fts_match(q: str) -> str:
    """Build a prefix FTS5 query from the VN-normalized, folded query."""
    toks = [t for t in normalize_query(fold(q)).replace('"', " ").split() if t]
    return " ".join(f'"{t}"*' for t in toks)
```

In `search()`, replace the line `qf = fold(q)` with:

```python
    qn = normalize_query(fold(q))
```

and in the nested `rank(x)` function replace the two comparisons that use `qf`:

```python
            0 if x["folded"] == qf else 1,
            0 if x["folded"].startswith(qf) else 1,
```

with:

```python
            0 if x["folded"] == qn else 1,
            0 if x["folded"].startswith(qn) else 1,
```

- [ ] **Step 4: Run the new test + the existing geocoder suite**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/geocoder && PYTHONPATH=. python3 -m pytest -q`
Expected: all pass (existing `test_fts_match_builds_prefix_query` still passes — plain queries normalize to themselves).

- [ ] **Step 5: Commit**

```bash
git add services/geocoder/app/main.py services/geocoder/tests/test_vnorm.py
git commit -m "feat(geocoder): normalize VN queries in search (q1->quan 1, strip house no.)"
```

---

## Task 3: `indexops.build_trigrams` + tests

**Files:**
- Create: `services/geocoder/indexops.py`
- Test: `services/geocoder/tests/test_indexops.py`

- [ ] **Step 1: Write the failing test**

Create `services/geocoder/tests/test_indexops.py`:

```python
"""Pure SQL index-op tests (no osmium): trigram build + street merge."""
import sqlite3

from indexops import build_trigrams, merge_streets


def _seed(rows):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE features(id INTEGER PRIMARY KEY, osm_id TEXT, name TEXT, folded TEXT,"
        " kind TEXT, lat REAL, lon REAL, extra TEXT, importance INTEGER DEFAULT 0,"
        " category TEXT, housenumber TEXT, street TEXT, city TEXT, district TEXT, region TEXT)")
    for i, r in enumerate(rows, 1):
        con.execute(
            "INSERT INTO features(id,osm_id,name,folded,kind,lat,lon,importance,district,city)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (i, f"n{i}", r["name"], r["folded"], r["kind"], r["lat"], r["lon"],
             r.get("importance", 0), r.get("district", ""), r.get("city", "")))
    con.commit()
    return con


def test_build_trigrams_populates_distinct_folded():
    con = _seed([{"name": "Nguyễn Huệ", "folded": "nguyen hue", "kind": "street", "lat": 10.0, "lon": 106.0},
                 {"name": "Nguyễn Huệ", "folded": "nguyen hue", "kind": "street", "lat": 10.1, "lon": 106.1}])
    build_trigrams(con)
    foldeds = {r[0] for r in con.execute("SELECT DISTINCT folded FROM trgm").fetchall()}
    assert foldeds == {"nguyen hue"}                      # deduped
    # the trigram 'ngu' maps to the folded string
    assert con.execute("SELECT count(*) FROM trgm WHERE g='ngu'").fetchone()[0] == 1


def test_merge_streets_collapses_same_name_within_area():
    con = _seed([
        {"name": "Lê Lợi", "folded": "le loi", "kind": "street", "lat": 10.0, "lon": 106.0, "district": "Quan 1", "importance": 10},
        {"name": "Lê Lợi", "folded": "le loi", "kind": "street", "lat": 10.2, "lon": 106.2, "district": "Quan 1", "importance": 12},
        {"name": "Lê Lợi", "folded": "le loi", "kind": "street", "lat": 11.0, "lon": 107.0, "district": "Quan 3", "importance": 8},
        {"name": "Chợ Bến Thành", "folded": "cho ben thanh", "kind": "poi", "lat": 10.0, "lon": 106.0},
    ])
    merge_streets(con)
    streets = con.execute("SELECT folded, district, lat, lon, importance FROM features WHERE kind='street' ORDER BY district").fetchall()
    assert len(streets) == 2                              # one per (folded, district)
    q1 = [s for s in streets if s["district"] == "Quan 1"][0]
    assert abs(q1["lat"] - 10.1) < 1e-9                   # averaged 10.0 & 10.2
    assert q1["importance"] == 12                         # max
    assert con.execute("SELECT count(*) FROM features WHERE kind='poi'").fetchone()[0] == 1  # poi untouched
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/geocoder && PYTHONPATH=. python3 -m pytest tests/test_indexops.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'indexops'`

- [ ] **Step 3: Create `indexops.py`**

```python
"""Pure SQLite index operations for the geocoder, separated from importer.py so
they can be unit-tested without osmium. Operate on an open sqlite3.Connection."""
from __future__ import annotations

import sqlite3

from app.vnorm import trigrams


def build_trigrams(con: sqlite3.Connection) -> None:
    """(Re)build the `trgm` similarity index over DISTINCT folded strings."""
    con.execute("DROP TABLE IF EXISTS trgm")
    con.execute("CREATE TABLE trgm(g TEXT, folded TEXT)")
    rows = con.execute("SELECT DISTINCT folded FROM features WHERE folded <> ''").fetchall()
    batch: list[tuple[str, str]] = []
    for r in rows:
        folded = r[0]
        for g in trigrams(folded):
            batch.append((g, folded))
        if len(batch) >= 10000:
            con.executemany("INSERT INTO trgm(g, folded) VALUES (?, ?)", batch)
            batch.clear()
    if batch:
        con.executemany("INSERT INTO trgm(g, folded) VALUES (?, ?)", batch)
    con.execute("CREATE INDEX idx_trgm_g ON trgm(g)")


def merge_streets(con: sqlite3.Connection) -> None:
    """Collapse same-name street segments within an admin area to one representative
    row (averaged location, max importance). A long street split across many OSM ways
    stops producing many near-duplicate hits."""
    con.executescript("""
        DROP TABLE IF EXISTS _street_rep;
        CREATE TEMP TABLE _street_rep AS
          SELECT MIN(id) AS keep_id, folded,
                 COALESCE(NULLIF(district, ''), city, '') AS area,
                 AVG(lat) AS alat, AVG(lon) AS alon, MAX(importance) AS imp
          FROM features WHERE kind='street'
          GROUP BY folded, COALESCE(NULLIF(district, ''), city, '');
        UPDATE features SET
          lat = (SELECT alat FROM _street_rep WHERE keep_id = features.id),
          lon = (SELECT alon FROM _street_rep WHERE keep_id = features.id),
          importance = (SELECT imp FROM _street_rep WHERE keep_id = features.id)
          WHERE id IN (SELECT keep_id FROM _street_rep);
        DELETE FROM features
          WHERE kind='street' AND id NOT IN (SELECT keep_id FROM _street_rep);
        DROP TABLE _street_rep;
    """)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/geocoder && PYTHONPATH=. python3 -m pytest tests/test_indexops.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add services/geocoder/indexops.py services/geocoder/tests/test_indexops.py
git commit -m "feat(geocoder): indexops — trigram index + street merge (pure, osmium-free)"
```

---

## Task 4: Trigram typo fallback in `main.py` search

**Files:**
- Modify: `services/geocoder/app/main.py` (`search` ~line 52)
- Test: `services/geocoder/tests/test_search_fixture.py`

- [ ] **Step 1: Write the failing test (with the shared fixture builder)**

Create `services/geocoder/tests/test_search_fixture.py`:

```python
"""Search/reverse tests against a seeded in-memory SQLite (no osmium)."""
import sqlite3

import pytest

import app.main as m
from app.vnorm import trigrams

SCHEMA = """
CREATE TABLE features(
  id INTEGER PRIMARY KEY, osm_id TEXT, name TEXT, folded TEXT,
  kind TEXT, lat REAL, lon REAL, extra TEXT, importance INTEGER DEFAULT 0,
  category TEXT, housenumber TEXT, street TEXT, city TEXT, district TEXT, region TEXT);
CREATE VIRTUAL TABLE features_fts USING fts5(folded, content='features', content_rowid='id', tokenize='unicode61');
CREATE VIRTUAL TABLE features_rtree USING rtree(id, minlat, maxlat, minlon, maxlon);
CREATE TABLE trgm(g TEXT, folded TEXT);
"""


def make_db(rows):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for i, r in enumerate(rows, 1):
        con.execute(
            "INSERT INTO features(id,osm_id,name,folded,kind,lat,lon,extra,importance,"
            "category,housenumber,street,city,district,region)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (i, r.get("osm_id", f"n{i}"), r["name"], m.fold(r["name"]), r["kind"], r["lat"], r["lon"],
             r.get("extra", ""), r.get("importance", 0), r.get("category", ""), "", "",
             r.get("city", ""), r.get("district", ""), ""))
    con.execute("INSERT INTO features_fts(rowid, folded) SELECT id, folded FROM features")
    con.execute("INSERT INTO features_rtree SELECT id, lat, lat, lon, lon FROM features")
    for row in con.execute("SELECT DISTINCT folded FROM features").fetchall():
        for g in trigrams(row["folded"]):
            con.execute("INSERT INTO trgm(g, folded) VALUES (?, ?)", (g, row["folded"]))
    con.commit()
    return con


@pytest.fixture
def seeded(monkeypatch):
    con = make_db([
        {"name": "Nguyễn Huệ", "kind": "street", "lat": 10.7740, "lon": 106.7040, "importance": 12},
        {"name": "Lê Lợi", "kind": "street", "lat": 10.7720, "lon": 106.6990, "importance": 11},
        {"name": "Quận 1", "kind": "boundary", "lat": 10.7700, "lon": 106.7000, "importance": 50},
        {"name": "Chợ Bến Thành", "kind": "poi", "lat": 10.7725, "lon": 106.6980, "importance": 5},
    ])
    monkeypatch.setattr(m, "_conn", con)
    return con


def test_typo_falls_back_to_trigram(seeded):
    res = m.search("nguyn hue", 5)          # internal typo, strict FTS returns 0
    assert res and res[0]["name"] == "Nguyễn Huệ"


def test_clean_query_does_not_need_fallback(seeded):
    res = m.search("le loi", 5)
    assert res and res[0]["name"] == "Lê Lợi"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/geocoder && PYTHONPATH=. python3 -m pytest tests/test_search_fixture.py::test_typo_falls_back_to_trigram -v`
Expected: FAIL — strict FTS returns 0 rows for `nguyn hue`, no fallback yet → empty result.

- [ ] **Step 3: Add `_trigram_fallback` and call it on a strict miss**

In `services/geocoder/app/main.py`, add this function just above `search()`:

```python
def _trigram_fallback(qn: str, limit: int,
                      bias: tuple[float, float] | None) -> list[dict]:
    """Fuzzy fallback when strict FTS returns nothing: rank candidate folded
    strings by trigram Jaccard similarity, then importance."""
    Q = trigrams(qn)
    if not Q:
        return []
    ph = ",".join("?" * len(Q))
    cand = db().execute(
        f"SELECT folded, COUNT(*) AS shared FROM trgm WHERE g IN ({ph}) "
        "GROUP BY folded ORDER BY shared DESC LIMIT 50", tuple(Q)).fetchall()
    keep: dict[str, float] = {}
    for r in cand:
        denom = len(Q) + len(trigrams(r["folded"])) - r["shared"]
        jac = r["shared"] / denom if denom else 0.0
        if jac >= 0.3:
            keep[r["folded"]] = jac
    if not keep:
        return []
    ph2 = ",".join("?" * len(keep))
    rows = db().execute(
        f"""SELECT f.osm_id, f.name, f.folded, f.kind, f.lat, f.lon, f.extra,
                   f.importance, f.category, f.housenumber, f.street, f.city,
                   f.district, f.region
            FROM features f WHERE f.folded IN ({ph2})""", tuple(keep)).fetchall()
    out = [dict(r) for r in rows]

    def rank(x) -> tuple:
        penalty = 0.0
        if bias is not None:
            penalty = haversine(bias[0], bias[1], x["lat"], x["lon"]) / 1000.0 * BIAS_PER_KM
        return (-keep.get(x["folded"], 0.0), -(x["importance"]) + penalty)

    out.sort(key=rank)
    return out[:limit]
```

Then, in `search()`, after the strict query produces `out`, add the fallback when it is empty. Find:

```python
    qf = fold(q)
    out = [dict(r) for r in rows]
```

(after Task 2 this reads `qn = normalize_query(fold(q))` then `out = [dict(r) for r in rows]`). Immediately **after** `out = [dict(r) for r in rows]`, insert:

```python
    if not out and len(qn) >= 3:
        out = _trigram_fallback(qn, limit, bias)
        for x in out:
            x.pop("folded", None)
            x.pop("importance", None)
        return out
```

(The existing strict path below — `out.sort(key=rank)` etc. — is unchanged and only runs when `out` is non-empty.)

- [ ] **Step 4: Run to verify it passes + full suite**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/geocoder && PYTHONPATH=. python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/geocoder/app/main.py services/geocoder/tests/test_search_fixture.py
git commit -m "feat(geocoder): trigram-similarity typo fallback on strict-FTS miss"
```

---

## Task 5: Reverse kind-preference + distance cap + boundary rank

**Files:**
- Modify: `services/geocoder/app/main.py` (`KIND_RANK` ~line 21, `reverse` ~line 125)
- Test: `services/geocoder/tests/test_search_fixture.py`

- [ ] **Step 1: Write the failing tests**

Append to `services/geocoder/tests/test_search_fixture.py`:

```python
def test_reverse_prefers_street_over_colocated_poi(seeded):
    # Bến Thành POI and Lê Lợi street are ~co-located; prefer the street.
    r = m.reverse(10.7721, 106.6991)["result"]
    assert r is not None and r["kind"] in ("street", "boundary", "place")


def test_reverse_returns_none_beyond_cap(monkeypatch):
    con = make_db([{"name": "Far POI", "kind": "poi", "lat": 21.0, "lon": 105.8}])  # Hanoi
    monkeypatch.setattr(m, "_conn", con)
    monkeypatch.setattr(m, "REVERSE_MAX_M", 5000.0)
    assert m.reverse(10.77, 106.70)["result"] is None    # ~1100 km away -> refused
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/geocoder && PYTHONPATH=. python3 -m pytest tests/test_search_fixture.py -k reverse -v`
Expected: FAIL — current `reverse()` returns the nearest POI and has no cap (the far POI is returned via the 0.3° fallback).

- [ ] **Step 3: Add boundary to KIND_RANK + the cap constant**

In `services/geocoder/app/main.py`, replace:

```python
KIND_RANK = {"place": 0, "street": 1, "poi": 2, "address": 3}
```

with:

```python
KIND_RANK = {"place": 0, "boundary": 1, "street": 2, "poi": 3, "address": 4}
REVERSE_MAX_M = float(os.environ.get("GEOCODER_REVERSE_MAX_M", "5000"))
```

- [ ] **Step 4: Rewrite `reverse()`**

Replace the whole `reverse()` function with:

```python
@app.get("/reverse")
def reverse(lat: float = Query(...), lon: float = Query(...)) -> dict:
    # expand the window up to the cap (~5 km ≈ 0.05°); within each band keep only
    # candidates inside REVERSE_MAX_M, then prefer street/place/boundary over a POI
    # at similar distance. Refuse anything beyond the cap.
    for d in (0.005, 0.02, 0.05):
        rows = db().execute(
            f"""SELECT {_FIELDS}
               FROM features_rtree t JOIN features f ON f.id = t.id
               WHERE t.minlat BETWEEN ? AND ? AND t.minlon BETWEEN ? AND ?""",
            (lat - d, lat + d, lon - d, lon + d),
        ).fetchall()
        cands = []
        for r in rows:
            dist = haversine(lat, lon, r["lat"], r["lon"])
            if dist <= REVERSE_MAX_M:
                cands.append((r, dist))
        if cands:
            best, dist = min(
                cands, key=lambda rd: (round(rd[1] / 100), KIND_RANK.get(rd[0]["kind"], 9), rd[1]))
            b = dict(best)
            b["distance_m"] = round(dist, 1)
            return {"lat": lat, "lon": lon, "result": b}
    return {"lat": lat, "lon": lon, "result": None}
```

- [ ] **Step 5: Run to verify they pass + full suite**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/geocoder && PYTHONPATH=. python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add services/geocoder/app/main.py services/geocoder/tests/test_search_fixture.py
git commit -m "feat(geocoder): reverse kind-preference + distance cap + boundary rank"
```

---

## Task 6: importer — log-scaled prominence, admin `area()`, wire indexops

**Files:**
- Modify: `services/geocoder/importer.py`
- Test: `services/geocoder/tests/test_indexops.py` (importance, guarded by osmium import)

- [ ] **Step 1: Write the failing importance test**

Append to `services/geocoder/tests/test_indexops.py`:

```python
def test_importance_population_is_log_scaled():
    import pytest
    pytest.importorskip("osmium")                 # importer imports osmium at module top
    from importer import importance
    big = importance({"place": "city", "population": "1000000"}, "place")
    mid = importance({"place": "city", "population": "100000"}, "place")
    small = importance({"place": "city", "population": "10000"}, "place")
    assert big > mid > small                       # smooth, not digit-count ties
```

- [ ] **Step 2: Run to verify it fails (where osmium is present) / skips (absent)**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/geocoder && PYTHONPATH=. python3 -m pytest tests/test_indexops.py::test_importance_population_is_log_scaled -v`
Expected: SKIP locally (no osmium). On the geocoder image / CI it FAILS first (digit-count makes 1e6 and 1e5 closer than expected) then passes after Step 3.

- [ ] **Step 3: Log-scale `importance()`**

In `services/geocoder/importer.py`, add `import math` (with the stdlib imports at the top), then replace the population block in `importance()`:

```python
    pop = tags.get("population", "")
    if pop.replace(".", "").isdigit():
        # log10-ish without importing math: digits of the population
        score += min(40, (len(pop.split(".")[0]) - 1) * 8)
```

with:

```python
    pop_raw = tags.get("population", "").replace(".", "").replace(",", "")
    if pop_raw.isdigit():
        score += min(40, round(math.log10(max(int(pop_raw), 1)) * 8))
```

- [ ] **Step 4: Add the admin `area()` handler**

In `services/geocoder/importer.py`, add the admin-weight map near the other weights (after `PLACE_WEIGHT`):

```python
# Admin boundary prominence by OSM admin_level (VN: 4=province/city, 6=district, 8=ward).
ADMIN_WEIGHT = {"4": 80, "6": 50, "8": 30}
```

Add an `area()` method to the `Handler` class (defining it triggers pyosmium's second-pass
multipolygon assembly automatically):

```python
    def area(self, a):
        if a.tags.get("boundary") != "administrative":
            return
        lvl = a.tags.get("admin_level")
        name = a.tags.get("name")
        if lvl not in ADMIN_WEIGHT or not name:
            return
        # representative point = center of the outer-ring bounding box
        minlat = minlon = 1e9
        maxlat = maxlon = -1e9
        for outer in a.outer_rings():
            for node in outer:
                lat, lon = node.lat, node.lon
                minlat, maxlat = min(minlat, lat), max(maxlat, lat)
                minlon, maxlon = min(minlon, lon), max(maxlon, lon)
        if minlat > maxlat:
            return
        oid = f"{'w' if a.from_way() else 'r'}{a.orig_id()}"
        self.batch.append((oid, name, fold(name), "boundary",
                           (minlat + maxlat) / 2, (minlon + maxlon) / 2, name,
                           ADMIN_WEIGHT[lvl], "admin_level_" + lvl, "", "", "", "", ""))
        self.count += 1
        if len(self.batch) >= 5000:
            self._flush()
```

(`node.lat`/`node.lon` are the ring node coordinates after area assembly. If the installed pyosmium
exposes them only via `node.location`, use `node.location.lat`/`node.location.lon`.)

- [ ] **Step 5: Wire `indexops` into `build()`**

In `build()`, after `h._flush()` + `db.commit()` and **before** the index/FTS/R*Tree `executescript`,
add the street merge; and after that `executescript`, build the trigram index. Concretely, change:

```python
    h = Handler(db)
    h.apply_file(pbf, locations=True, idx="flex_mem")
    h._flush()
    db.commit()

    # Rank: places first, then streets, then POIs/addresses (for result ordering)
    db.executescript("""
        CREATE INDEX idx_folded ON features(folded);
        CREATE INDEX idx_category ON features(category);
        CREATE INDEX idx_osmid ON features(osm_id);
        CREATE VIRTUAL TABLE features_fts USING fts5(
          folded, content='features', content_rowid='id', tokenize='unicode61');
        INSERT INTO features_fts(rowid, folded) SELECT id, folded FROM features;
        CREATE VIRTUAL TABLE features_rtree USING rtree(id, minlat, maxlat, minlon, maxlon);
        INSERT INTO features_rtree SELECT id, lat, lat, lon, lon FROM features;
    """)
    db.commit()
```

to:

```python
    h = Handler(db)
    h.apply_file(pbf, locations=True, idx="flex_mem")
    h._flush()
    db.commit()

    from indexops import merge_streets, build_trigrams
    merge_streets(db)              # collapse same-name street segments before indexing
    db.commit()

    # Rank: places first, then streets, then POIs/addresses (for result ordering)
    db.executescript("""
        CREATE INDEX idx_folded ON features(folded);
        CREATE INDEX idx_category ON features(category);
        CREATE INDEX idx_osmid ON features(osm_id);
        CREATE VIRTUAL TABLE features_fts USING fts5(
          folded, content='features', content_rowid='id', tokenize='unicode61');
        INSERT INTO features_fts(rowid, folded) SELECT id, folded FROM features;
        CREATE VIRTUAL TABLE features_rtree USING rtree(id, minlat, maxlat, minlon, maxlon);
        INSERT INTO features_rtree SELECT id, lat, lat, lon, lon FROM features;
    """)
    build_trigrams(db)             # hand-built trigram-similarity index
    db.commit()
```

- [ ] **Step 6: Verify syntax + the importance test where osmium is available**

Run:
```bash
cd /Users/nullshift-labs/dev/nullmap/services/geocoder
python3 -c "import ast; ast.parse(open('importer.py').read()); print('importer.py parses')"
PYTHONPATH=. python3 -m pytest tests/test_indexops.py -q
```
Expected: `importer.py parses`; the importance test passes where osmium is installed, skips otherwise.

- [ ] **Step 7: Commit**

```bash
git add services/geocoder/importer.py services/geocoder/tests/test_indexops.py
git commit -m "feat(geocoder): admin boundary area() + log-scaled prominence + indexops wiring"
```

---

## Task 7: Docs — geocoder README + ops reindex verification

**Files:**
- Modify: `services/geocoder/README.md`
- Modify: `docs/runbook-ops.md`

- [ ] **Step 1: Add a geocoder README note**

Append to `services/geocoder/README.md`:

```markdown
## Accuracy features (③a)

- **VN query normalization** (`app/vnorm.py`): `q1`→`quan 1`, `p3`→`phuong 3`, `tp`/`tx` expansion,
  leading house-number stripped, leading `duong`/`đ.` dropped. Applied to every search.
- **Typo tolerance**: a hand-built trigram-similarity index (`trgm` table, pg_trgm-style Jaccard) is
  queried only when the strict FTS prefix returns nothing (so common-path latency is unchanged).
  Adds roughly +50–100 MB to the index, built over distinct folded names.
- **Admin boundaries**: `boundary=administrative` at admin_level 4/6/8 (province/district/ward) are
  indexed as `kind='boundary'` centroids, so `Quận 1` / `Bình Thạnh` / `Phường Bến Nghé` are findable.
- **Reverse**: prefers street/place/boundary over a co-located POI and refuses matches beyond
  `GEOCODER_REVERSE_MAX_M` (default 5000 m) — set the env var to widen for sparse rural areas.
- **Prominence**: population is parsed to an int and log10-scaled; same-name street segments within an
  admin area are merged to one representative point.
```

- [ ] **Step 2: Add a reindex-verification block to the ops runbook**

In `docs/runbook-ops.md`, under the "Manual recovery" section, append:

```markdown
## Verify a geocoder reindex (③a accuracy)

After `make geo-index` (or a `refresh.sh` run), sanity-check the new index:

```bash
DB=services/geocoder/data/geocoder.db
sqlite3 "$DB" "SELECT count(*) FROM features WHERE kind='boundary';"   # > 0 (admin areas indexed)
sqlite3 "$DB" "SELECT count(*) FROM trgm;"                              # > 0 (typo index built)
# a known long street should now be a single representative row per district:
sqlite3 "$DB" "SELECT district, count(*) FROM features WHERE folded='le loi' AND kind='street' GROUP BY district;"
```

Then spot-check the service: `curl 'localhost:2322/geocode?q=nguyn+hue'` (typo) returns Nguyễn Huệ,
and `curl 'localhost:2322/geocode?q=q1'` returns Quận 1.
```

- [ ] **Step 3: Commit**

```bash
git add services/geocoder/README.md docs/runbook-ops.md
git commit -m "docs(geocoder): document ③a accuracy features + reindex verification"
```

---

## Task 8: Final verification

- [ ] **Step 1: Full geocoder suite**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/geocoder && PYTHONPATH=. python3 -m pytest -q`
Expected: all pass (osmium-dependent tests skip where pyosmium is absent; vnorm/indexops/search-fixture all run).

- [ ] **Step 2: importer + indexops import cleanly together**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/geocoder && python3 -c "import ast; ast.parse(open('importer.py').read()); ast.parse(open('indexops.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Adapter + normalizer suites unaffected**

Run:
```bash
cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest -q
cd /Users/nullshift-labs/dev/nullmap/services/normalizer && python3 -m pytest -q
```
Expected: adapter + normalizer all pass (no cross-impact).

- [ ] **Step 4: Branch log**

Run: `git log --oneline main..HEAD`
Expected: the spec commit + Tasks 1-7 commits.

---

## Self-Review (completed by plan author)

- **Spec coverage:** §1 normalization (T1 vnorm + T2 wiring) ✓ · §2 hand-built trigram typo (T3 indexops + T4 fallback) ✓ · §3 admin centroid area() (T6) ✓ · §4 reverse kind-pref + cap + boundary rank (T5) ✓ · §5 log-scale prominence + street merge (T3 merge + T6 importance) ✓ · testing via vnorm/indexops/fixture all osmium-free ✓ · README + reindex doc (T7) ✓. All success criteria mapped.
- **Placeholder scan:** none — every file is given in full or via exact find/replace anchors; the one judgment note (pyosmium `node.lat` vs `node.location.lat`) gives both forms explicitly.
- **Type/name consistency:** `normalize_query`/`trigrams` defined once in `app/vnorm.py` (T1) and imported by `main.py` (T2/T4) and `indexops.py` (T3); `build_trigrams`/`merge_streets` defined in `indexops.py` (T3) and called by `importer.build()` (T6); `_trigram_fallback(qn, limit, bias)` defined (T4) is invoked from `search()` with the same signature; `KIND_RANK` gains `boundary` (T5) and the same map is read by `reverse()`; the `trgm(g, folded)` schema is identical in `indexops.build_trigrams`, the fixture `make_db`, and the `_trigram_fallback` query; `REVERSE_MAX_M` defined (T5) is the same name the test monkeypatches.
```
