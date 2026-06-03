# Geocoder Accuracy (VN) — Design Spec

**Date:** 2026-06-03
**Sub-project:** ③a of the NullMaps upgrade program (①✅ ②✅ → **③a Geocoder accuracy** → ③b Routing depth → ④ Visual)
**Branch:** `feat/geocoder-accuracy`
**Status:** Approved design → ready for implementation plan

## Context

Goal: lift real-world typeahead/geocode hit-rate and reverse-geocode trustworthiness for the core HCMC
motorbike-dispatch use case, inside the existing lightweight SQLite geocoder (no Photon swap).

**Confirmed from code (read, not assumed):**

- `services/geocoder/importer.py` has only `node()` and `way()` handlers — **no `area()`/relation handler**,
  so VN wards/districts/provinces (OSM relations) and multipolygon areas are entirely unindexed.
- `importer.py:59-71` `importance()` approximates population by **digit count**
  (`(len(pop.split(".")[0]) - 1) * 8`), so 100000 and 999999 score identically.
- `importer.py` schema already has `housenumber/street/city/district/region` columns from `addr:*` tags,
  but most POIs/streets lack `addr:*`.
- `services/geocoder/app/main.py:41-44` `fts_match()` only folds + builds prefix `"token"*` queries —
  **no typo tolerance, no abbreviation expansion**; `q1` folds to the literal token `q1`.
- `main.py:125-140` `reverse()` expands the R*Tree window through `0.005, 0.02, 0.08, 0.3` degrees and
  returns the **nearest feature of any kind**; the final `0.3°` (~33 km) fallback can return absurd matches.
- FTS5 is `tokenize='unicode61'`. The bundled SQLite (3.53) has the FTS5 `trigram` tokenizer, but it does
  **substring** matching only — it does NOT tolerate an internal typo (verified: `MATCH 'nguyn hue'` on
  `'nguyen hue'` → 0 rows). `spellfix1` is NOT bundled. Real fuzzy matching therefore needs either
  spellfix1 (an extension) or a hand-built trigram-similarity index (chosen).

**Design decisions (locked with operator):**

- Typo tolerance via a **hand-built trigram-similarity index** (pg_trgm-style: a `trgm(g, folded)` table
  + Jaccard ranking). NOT the FTS5 `trigram` tokenizer — empirically that is substring-only and does not
  catch an internal typo (`MATCH 'nguyn hue'` on `'nguyen hue'` returns 0 rows) — and not spellfix1
  (avoids compiling a SQLite extension). The hand-built approach was verified to score the target typos
  ≥0.55 Jaccard while an unrelated query scores 0.
- Admin relations: **centroid + searchable only** (index boundary centroids; no polygon storage, no
  point-in-polygon reverse enrichment).
- Scope: all five items below (query normalization, typo, admin centroids, reverse kind-pref + cap,
  prominence + street merge).

**Explicitly out of scope:** point-in-polygon reverse enrichment, spellfix1, Photon swap, the ③b routing
items (separate sub-project). Traffic-aware ETAs are deferred (no historical speed dataset).

## Goals / Success Criteria

1. `q1`, `q.1`, `p3`, `tp hcm`, and a leading house number (`123 nguyen hue`) normalize so they match the
   intended street/admin feature.
2. A one-character typo (`nguyn hue`) returns the right result via a trigram fallback, with the strict
   common path unchanged when it already matches.
3. `Quận 1`, `Bình Thạnh`, `Phường Bến Nghé` are findable and rank as prominent admin features.
4. Reverse geocoding refuses matches beyond a configurable cap and prefers street/place/boundary over a
   random POI at similar distance.
5. Population-based prominence scales smoothly (log10 on a parsed int); same-name street segments collapse
   to one representative point.
6. All of the above is covered by tests that run without `osmium` (a seeded fixture DB).

## Design

### Unit boundaries

- **New** `services/geocoder/app/vnorm.py` — pure VN query-normalization functions (no DB, no FastAPI), so
  they can be unit-tested in isolation and reused. One responsibility: turn a folded user query into a
  normalized folded query.
- **Modify** `services/geocoder/app/main.py` — wire `vnorm` into `fts_match`/`search`, add the trigram
  fallback, and harden `reverse()`.
- **Modify** `services/geocoder/importer.py` — trigram FTS table, `area()` admin handler, log-scaled
  prominence, post-import street merge.
- **New** `services/geocoder/tests/test_vnorm.py` and `services/geocoder/tests/test_search_fixture.py`.

### 1 — Query normalization (`app/vnorm.py` + `app/main.py`)

`normalize_query(folded: str) -> str` applies, in order, on an already-folded string:

1. Strip a leading house number: `re.sub(r'^\s*\d+\s*[a-z]?\s+', '', s)` → `123 nguyen hue` → `nguyen hue`,
   `12a le loi` → `le loi`.
2. Admin abbreviation + number: `re.sub(r'\bq\.?\s*(\d+)\b', r'quan \1', s)` and the same for
   `p` → `phuong`. (Only when followed by a digit, so a bare `p`/`q` token in a name is untouched.)
3. City/town markers: `re.sub(r'\btp\.?\b', 'thanh pho', s)`, `re.sub(r'\btx\.?\b', 'thi xa', s)`.
4. Drop a leading street-type word so the bare OSM street name matches:
   `re.sub(r'^(duong|d\.)\s+', '', s)` → `duong le loi` / `d. le loi` → `le loi`.

`main.py`: `fts_match(q)` computes `qn = normalize_query(fold(q))` and builds prefix tokens from `qn`.
`search()` computes `qn` once and uses it for both the FTS match and the exact/prefix comparisons in
`rank()` (today it compares against `fold(q)`; switch to `qn`).

### 2 — Typo tolerance (hand-built trigram-similarity index)

A `trigrams(s)` helper lives in `app/vnorm.py` (one definition, imported by both `main.py` and
`importer.py`): pad `s` as `"  " + s + " "` and return the set of 3-grams. (pg_trgm-style padding.)

- **importer:** after the base inserts, build a trigram index over the **distinct** folded strings (many
  features share a name, so dedup keeps it small):
  `CREATE TABLE trgm(g TEXT, folded TEXT);` then for each `DISTINCT folded` in `features`, insert one
  `(g, folded)` row per trigram; `CREATE INDEX idx_trgm_g ON trgm(g);`
- **main.py `search()`:** after the strict prefix query, if it returns **zero rows** and `len(qn) >= 3`:
  1. `Q = trigrams(qn)`.
  2. `SELECT folded, COUNT(*) AS shared FROM trgm WHERE g IN (<Q placeholders>) GROUP BY folded
     ORDER BY shared DESC LIMIT 50`.
  3. For each candidate, `jaccard = shared / (len(Q) + len(trigrams(folded)) - shared)`; keep those with
     `jaccard >= 0.3`.
  4. Load the features for those folded strings (`WHERE folded IN (...)`), attach `jaccard`, and rank by
     `(-jaccard, -importance, bias_penalty)`. Return the top `limit`.

  The strict path is untouched, so common-path latency is unchanged; the fallback only runs on a miss.

### 3 — Admin centroid indexing (`importer.py` `area()` handler)

- Add area assembly so relations become polygons, and an `area(self, a)` handler that indexes
  `boundary=administrative` features with a `name` and `admin_level in {"4","6","8"}` (VN
  province/city, district, ward) — plus named multipolygons already covered by `classify` (parks).
- Store as `kind='boundary'`, with `lat/lon` = the center of the area's outer-ring bounding box
  (a representative point; no polygon geometry persisted).
- `importance` for boundaries by level: `{"4": 80, "6": 50, "8": 30}` (province > district > ward), so
  admin names rank above incidental POIs sharing a token.
- `classify()`/`_add()` extended to recognise the `boundary` kind; `KIND_RANK` in `main.py` gains
  `"boundary"` between `place` and `street`.

### 4 — Reverse kind-preference + distance cap (`main.py` `reverse()`)

- New env `GEOCODER_REVERSE_MAX_M` (default `5000`). The R*Tree window expansion stops at the degree band
  that corresponds to the cap; if no candidate is within `GEOCODER_REVERSE_MAX_M`, return `result: None`
  (refuse absurd far matches) instead of the old 33 km fallback.
- Among candidates, rank by `(round(distance_m / 100), KIND_RANK[kind], distance_m)` — within a 100 m band,
  prefer `place`/`boundary`/`street` over `poi`/`address`; otherwise nearest wins. Return the best.

### 5 — Prominence log-scale + street merge (`importer.py`)

- `importance()`: parse population to an int (`int(re.sub(r'[.,]', '', pop))` guarded by `isdigit`), then
  `score += min(40, round(math.log10(max(pop, 1)) * 8))` (add `import math`). Smooth scaling; malformed
  values are ignored.
- Post-import **street merge** in `build()`, after the base inserts and before building FTS/R*Tree: for
  `kind='street'`, group by `(folded, COALESCE(NULLIF(district,''), city, ''))` and collapse each group to
  one representative row — `AVG(lat)`, `AVG(lon)`, `MAX(importance)`, keep the first `osm_id`/`name` — then
  delete the redundant rows. (A long street split across many OSM ways stops producing many near-duplicate
  hits and a better representative point for reverse-on-a-road.)

### Testing

- `tests/test_vnorm.py` — `normalize_query`: `q1`→`quan 1`, `q.1`→`quan 1`, `p3`→`phuong 3`,
  `123 nguyen hue`→`nguyen hue`, `tp hcm`→`thanh pho hcm`, `duong le loi`→`le loi`, and a bare `q`/`p`
  token inside a name is untouched.
- `tests/test_search_fixture.py` — build a tiny temp SQLite with the production schema + the `trgm` table,
  seed ~20 rows (a prominent street, a same-name minor street, an admin boundary, a few POIs at known
  coordinates) and populate `trgm` from their folded strings, then `monkeypatch` `main.db()` to it and
  assert: strict ranking; the trigram fallback fires only on a typo (`nguyn hue` → Nguyễn Huệ) and not on
  a clean hit; reverse prefers street/boundary over a co-located POI; reverse returns `None` beyond the cap.
- `importance()` log-scale asserted directly (1e6 > 1e5 > 1e4).
- `area()` admin assembly and the street-merge SQL are validated by the fixture/pure-logic tests where
  possible; a full reindex verification (counts of `kind='boundary'`, dedup of a known street) is documented
  as a manual step in `docs/runbook-ops.md`.

## Risks & Mitigations

- **Trigram index adds storage** — built over *distinct* folded strings to stay modest (roughly +50–100 MB
  on a ~56 MB DB); acceptable on the box; documented in the geocoder README box-sizing note. If it ever
  grows uncomfortable, cap it to `kind in ('place','street','boundary')` folded strings.
- **`area()` assembly is slower/heavier** than node/way passes on the full VN extract — it runs only during
  the off-peak reindex (now health-gated + rollback-protected by `refresh.sh`).
- **Over-eager normalization** could mangle a legitimate name (e.g. a street literally named with `q`/`p`
  + digit) — mitigated by only expanding the abbreviation-plus-digit pattern and keeping the trigram
  fallback as a safety net; covered by a "don't touch bare token" test.
- **Reverse cap too tight in rural areas** — the cap is env-configurable; default 5 km suits HCMC and most
  suburban use, and returning `None` is safer than a 33 km match for a routing input.

## Definition of Done

- All success criteria met; `python3 -m pytest` green in `services/geocoder` (osmium-dependent tests still
  skip where pyosmium is absent; the new fixture tests do not need osmium).
- A reindex on the box produces `kind='boundary'` rows, a populated `trgm` index, and de-duplicated
  streets; `make backup-test` still passes against the refreshed index.
- Geocoder README notes the trigram-similarity index, admin boundaries, and the reverse cap env var.
