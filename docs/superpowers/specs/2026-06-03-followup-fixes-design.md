# Follow-up Fixes — Design Spec

**Date:** 2026-06-03
**Sub-project:** Follow-ups found during the prod deploy of the upgrade program.
**Branch:** `feat/followup-fixes`
**Status:** Approved (decisions made) → implement

## Context

Two issues surfaced when the program went live on the box:

1. **Admin-boundary search misses urban districts** — `q1` → "Quán 19" not "Quận 1". **Root cause (verified
   on the box index): Vietnam abolished the district tier in its 2025 administrative reform.** The current
   `vietnam-latest.osm.pbf` has `admin_level_4` = 33 (provinces/cities) and `admin_level_6` = 3334, all
   **Xã/Phường (commune/ward)** — there are **no Quận/Huyện** at all. So "Quận 1" is no longer an OSM admin
   unit; the geocoder indexes the current units correctly. People (and existing apps) still type "Q1".
   → **Decision: ship a legacy-district lookup** so colloquial "Quận 1" / "Bình Thạnh" still resolve.

2. **Vietnamese turn-by-turn falls back to English** — the Valhalla image lacks a `vi-VN` Odin narrative
   locale (fr-FR/de-DE work; only the binary's compiled locale set matters; the field is correct).
   → **Decision: generate Vietnamese narrative in the adapter** from the maneuver `type` + `street_names`
   (+ `roundabout_exit_count`), no Valhalla rebuild.

**Confirmed from code/data:**

- Valhalla maneuvers expose `type` (int), `street_names` (list), `roundabout_exit_count`; the existing
  `_MANEUVER` dict in `main.py` maps the main types to Google strings.
- `importer.build()` (after ③a) does the osmium pass → `indexops.merge_streets` → build FTS/R*Tree/trgm.
- `services/geocoder/indexops.py` already holds pure, osmium-free index ops (testable).
- The geocoder's `search()` ranks by `(exact, prefix, -importance, bm25)`; legacy districts at high
  importance + exact fold-match will win over POIs.

## Goals / Success Criteria

1. `q1` / `quan 1` / `quận 1` resolves to a "Quận 1" point (legacy lookup), likewise the other common
   HCMC/Hà Nội/Đà Nẵng pre-2025 urban districts.
2. Directions return Vietnamese `html_instructions` by default (e.g. "Rẽ trái vào Lê Thánh Tôn",
   "Vào vòng xoay, đi lối ra thứ 2"); `?language=en` keeps Valhalla's English.
3. Both are covered by tests that need neither osmium nor a live Valhalla.

## Design

### A — Legacy district lookup (geocoder)

- **New** `services/geocoder/legacy_districts.json` — `[{ "name", "lat", "lon", "city" }, ...]` for the
  pre-2025 urban districts people still use: HCMC (Quận 1–12 incl. old Q2/Q9, Bình Thạnh, Phú Nhuận,
  Tân Bình, Tân Phú, Gò Vấp, Bình Tân, Thủ Đức, + huyện Củ Chi/Hóc Môn/Bình Chánh/Nhà Bè/Cần Giờ),
  Hà Nội core (Hoàn Kiếm, Ba Đình, Đống Đa, Hai Bà Trưng, Cầu Giấy, Thanh Xuân, Tây Hồ, Hoàng Mai,
  Long Biên, Hà Đông, Nam/Bắc Từ Liêm), Đà Nẵng (Hải Châu, Thanh Khê, Sơn Trà, Ngũ Hành Sơn, Liên Chiểu,
  Cẩm Lệ). Coordinates are approximate representative points (good enough for centering/bias).
- **`indexops.insert_legacy_districts(con, path)`** (new, pure, osmium-free): load the JSON and insert
  each as a `features` row — `kind='boundary'`, `name`, `folded=fold(name)`, lat, lon,
  `importance=60` (above wards' 50, below province's 80), `category='legacy_district'`, `extra=city`. Uses
  the same `fold()` as the service. Idempotent (delete prior `category='legacy_district'` first).
- **`importer.build()`**: call `insert_legacy_districts(db, "legacy_districts.json")` after
  `merge_streets`, before the FTS/R*Tree/trgm build, so the legacy rows are searchable and trigram-indexed.
- **Dockerfile**: `COPY legacy_districts.json .` so the file is in the build image used for reindex.
- Effect: `normalize_query("q1")` → `"quan 1"` exactly matches the legacy "Quận 1" (`folded="quan 1"`),
  tier-0 exact + importance 60 → ranks first.

### B — Vietnamese narrative (adapter)

- **New** `services/adapter/app/vinarrative.py` — `vi_instruction(maneuver: dict) -> str`:
  - `street = (maneuver.get("street_names") or [None])[0]` (first name; avoids the "Đường X" dup).
  - Map `maneuver["type"]` → a Vietnamese template, appending `" vào {street}"` / `" trên {street}"`
    where a street applies. Covered types (Valhalla Odin enum): 1–3 start, 4–6 destination, 7 becomes,
    8 continue, 9/16 slight, 10/15 turn, 11/14 sharp, 12/13 u-turn, 17 ramp-straight, 18/20 ramp-right,
    19/21 ramp-left, 22 stay-straight, 23/24 keep, 25 merge, 26 roundabout-enter (uses
    `roundabout_exit_count` → "đi lối ra thứ N"), 27 roundabout-exit, 28/29 ferry. Unknown/0 → fall back
    to `maneuver.get("instruction", "")` (Valhalla's English) so there is always text.
- **`main.py`**: `directions()` computes `vi = language_for(request).lower().startswith("vi")`; thread it
  into `build_route` → `build_steps(leg, leg_coords, vi)`. In `build_steps`, set
  `html_instructions = vi_instruction(mv) if vi else mv.get("instruction", "")`. The maneuver `type` /
  `maneuver` Google string is unchanged. Default (vi-VN) → Vietnamese; `?language=en` → English.

### Tests

- **`services/geocoder/tests/test_indexops.py`**: `insert_legacy_districts` on a fixture db + a tiny
  legacy JSON → a "Quận 1" row exists with `folded="quan 1"`, `kind='boundary'`, `importance=60`; running
  it twice doesn't duplicate (idempotent).
- **`services/geocoder/tests/test_search_fixture.py`**: seed the legacy "Quận 1" + a "Quán 19" POI →
  `search("q1")` returns "Quận 1" first.
- **`services/adapter/tests/test_vinarrative.py`**: `vi_instruction` for type 15 + street → "Rẽ trái vào
  Lê Thánh Tôn"; type 26 with `roundabout_exit_count=2` → contains "lối ra thứ 2"; type 12 → "Quay đầu";
  unknown type → returns the maneuver's English `instruction`.
- **`services/adapter/tests/test_directions_matrix.py`**: directions default → first step
  `html_instructions` is Vietnamese; `?language=en` → English (Valhalla passthrough).

## Deploy

- **A** needs a **geocoder image rebuild + reindex** on the box (the importer changed) — ~7 min, like ③a.
- **B** is an **adapter redeploy** only (light).

## Out of scope

Precise legacy-district polygons (points only), full national district coverage (major cities only),
Valhalla rebuild for vi, non-Vietnamese narrative locales.
