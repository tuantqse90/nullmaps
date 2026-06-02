# Quick Wins Bundle — Design Spec

**Date:** 2026-06-02
**Sub-project:** ① of the NullMaps upgrade program (① Quick wins → ② Hardening → ③ Accuracy → ④ Visual)
**Branch:** `feat/quick-wins-bundle`
**Status:** Approved design → ready for implementation plan

## Context

NullMaps is a self-hosted, Vietnam-only, single-operator Google/Goong replacement (see `CLAUDE.md`).
All five phases ship and work. A grounded subsystem survey identified a cluster of high-impact,
low-effort upgrades that are mostly independent and carry near-zero risk. This spec bundles them.

**Confirmed facts (read from code, not assumed):**

- Adapter runs a **single uvicorn worker** — `services/adapter/Dockerfile:10` has no `--workers`, and
  `services/adapter/app/main.py:115` documents the in-process rate-limit/metrics dicts as single-worker.
  → In-process caching is correct; Redis is unnecessary.
- `directions()` builds the Valhalla payload at `main.py:246-249` and **never sets a language** → turn
  instructions come back in English.
- `/metrics` (`main.py:145-146`) has **no `require_key`** → leaks per-key counts if the gateway is bypassed.
- `valhalla()` / `geocoder()` / `maybe_normalize()` (`main.py:183/192/207`) each open a **fresh
  `httpx.AsyncClient` per request** → no keep-alive to the engines.
- `maybe_normalize()` uses a **12s timeout** (`main.py:207`) on a path also used by per-keystroke autocomplete.
- The SDK (`client/nullmaps.js`) **does not import or register pmtiles**; `map()` points at
  `${base}/style.json` (tiles via Martin, not `pmtiles://`). The PMTiles protocol is unregistered, so any
  `pmtiles://` source a consumer adds would silently fail — the exact failure `CLAUDE.md` warns about.
- The demo (`services/tiles/style/index.html:33`) hardcodes `/style.json`; `style-dark.json` is fully
  authored and gateway-cached but unreachable from the demo.

**Design decisions (locked with operator):**

- Cache backend: **in-process `cachetools` TTL** (not Redis).
- `vi` turn-by-turn: **default `vi-VN`, overridable via `?language=`** (behavior change accepted).
- Scope: full bundle (all four item groups below).

**Explicitly out of scope** (deferred to keep the bundle tight):

- Bounding cardinality of the rate-limit / metrics dicts → ② Hardening.
- Redis-backed rate-limit / multi-replica concerns → not needed at one worker.
- Google-shaped error bodies on all failure paths → ② Hardening.

## Goals / Success Criteria

1. Every directions response returns Vietnamese turn-by-turn instructions by default; `?language=en`
   still works.
2. Repeated geocode/reverse/autocomplete/nearby/detail queries are served from an in-process cache;
   the engine is hit once per (path, params) within the TTL window.
3. One shared `httpx.AsyncClient` is reused across requests (keep-alive to the engines).
4. `/metrics` requires the API key.
5. The SDK registers the PMTiles protocol idempotently before creating a map, staying dependency-free.
6. The demo respects `prefers-color-scheme` and offers a light/dark toggle; `/style-dark.json` is documented.
7. The autocomplete normalization path can't hang on a slow LLM (≤2s, fail-open).
8. Docs reflect reality: SQLite geocoder (not Photon), the real endpoint surface, and shipped terrain.

## Design

### Item 1 — Vietnamese turn-by-turn (`adapter/app/main.py`)

In `directions()`, after the payload is built (`main.py:246`), attach directions options:

```python
LANG = {"vi": "vi-VN", "vi-vn": "vi-VN", "en": "en-US", "en-us": "en-US"}

def language_for(request: Request) -> str:
    raw = (request.query_params.get("language") or "").lower()
    return LANG.get(raw, raw or "vi-VN")  # default vi-VN; unknown codes pass through, Odin falls back to en-US
```

Set `payload["directions_options"] = {"units": "kilometers", "language": language_for(request)}`
(fold the existing `units` in). Applies to both `/route` and `/optimized_route`.
Distance Matrix produces no narrative → unchanged.

Valhalla bundles a `vi-VN` locale; an unknown locale is handled by Odin (defaults to `en-US`), so no
client-side validation is required.

### Item 2 — Shared httpx client + response cache (`adapter/app/main.py`, `requirements.txt`)

**Shared client.** Create one module-level `httpx.AsyncClient` on startup, close on shutdown:

```python
@app.on_event("startup")
async def _startup():
    app.state.http = httpx.AsyncClient()

@app.on_event("shutdown")
async def _shutdown():
    await app.state.http.aclose()
```

Rewrite `valhalla()`, `geocoder()`, `maybe_normalize()` to use `app.state.http`, passing the per-call
timeout as an argument (`client.post(..., timeout=20)`, etc.) so existing timeouts are preserved.

**Cache.** Add `cachetools` to `requirements.txt`. A single `TTLCache(maxsize=2048, ttl=120)` wraps the
`geocoder()` read path:

```python
_geo_cache = TTLCache(maxsize=2048, ttl=120)

async def geocoder(path, params):
    key = (path, frozenset(params.items()))
    if key in _geo_cache:
        return _geo_cache[key]
    result = await _geocoder_fetch(path, params)  # the current body, using app.state.http
    _geo_cache[key] = result
    return result
```

- Caches `/geocode`, `/reverse`, `/autocomplete`, `/nearby`, `/detail` (every geocoder call routes
  through `geocoder()`).
- Does **not** cache `/route` / `/sources_to_targets` (dynamic, large, low repetition).
- A fresh process after deploy starts with an empty cache; TTL=120s bounds staleness after a re-import.
- Single-worker asyncio: a check-then-set race between two concurrent identical requests only does the
  fetch twice — harmless. No lock needed.
- Errors raise as today (an `HTTPException` from `_geocoder_fetch` propagates before the cache write), so
  failures are not cached.

### Item 3 — PMTiles helper (SDK) + gate `/metrics` (adapter)

**SDK** (`client/nullmaps.js`). Add an idempotent registrar and call it before map creation:

```js
let _pmtilesRegistered = false;
function registerPmtilesProtocol(maplibregl, pmtiles) {
  if (_pmtilesRegistered) return;
  const lib = pmtiles || (typeof globalThis !== "undefined" ? globalThis.pmtiles : undefined);
  if (!lib || !maplibregl?.addProtocol) return; // no-op when pmtiles absent
  maplibregl.addProtocol("pmtiles", new lib.Protocol().tile);
  _pmtilesRegistered = true;
}
```

Call `registerPmtilesProtocol(maplibregl, opts.pmtiles)` at the top of `map()` and `staticImage()`
(read `pmtiles` from `opts`, falling back to the global). Keeps the SDK dependency-free; harmless when
the default Martin-served style is used, correct when a consumer adds a `pmtiles://` source. Also export
the function for manual use.

**Adapter** (`main.py`). Add `require_key(request)` to `/metrics`:

```python
@app.get("/metrics")
def metrics(request: Request):
    require_key(request)
    ...
```

The Prometheus scraper sends `?key=` or `X-API-Key`. The gateway keeps gating it too (defense in depth).

### Item 4 — Dark toggle, normalize timeout cap, docs truth-up

**Demo** (`services/tiles/style/index.html`). Pick the initial style from
`window.matchMedia("(prefers-color-scheme: dark)")`, and add a small toggle button that calls
`map.setStyle("/style-dark.json" | "/style.json")`. Re-add `NavigationControl` after style swaps if
needed (or add once and let MapLibre preserve it). Document `/style-dark.json` as a first-class endpoint
in `services/tiles/README.md`.

**Normalize timeout** (`main.py`). Give `maybe_normalize()` a `timeout` parameter. The autocomplete
caller passes `timeout=2`; the forward-geocode caller passes `timeout=8`. Fail-open behavior (return raw
text on `httpx.HTTPError`/timeout) is preserved.

**Docs truth-up:**

- `main.py` — delete the stale "Pending Phase 3 (Photon) — return a 503" block in the module docstring
  (`main.py:11-14`); geocoding is live. Trim the FastAPI `description` to match the real surface.
- `docker-compose.yml` — purge stale 503 / Photon comments.
- `README.md` — replace the box-sizing line "Phase 3 Photon/ES, +several GB, hungriest component" with the
  real SQLite geocoder figures (~56 MB index, 256 MB mem_limit) and a "Photon = prod swap-in" footnote;
  replace "all 4 endpoints" with the real surface (directions, distancematrix, geocode/reverse,
  autocomplete, nearbysearch, place/details, plus `/v1/isochrone`, `/v1/snap`, `/metrics`); add a terrain
  note (hillshade + contours ship, built by `infra/build-hillshade.sh` / `build-contour.sh`).
- `docs/architecture.md` — geocoder = SQLite FTS5/R*Tree (not Photon); add gateway, normalizer, and the
  terrain sources to the diagram/description.

### Tests (`services/adapter/tests/`, `requirements.txt`)

Add `pytest` to `requirements.txt` (currently missing despite tests existing). New/updated tests, mocking
the engines as the existing tests already do:

1. `directions` passes `directions_options.language == "vi-VN"` by default and `"en-US"` for `?language=en`.
2. `/metrics` returns 403 without a key and 200 with the key.
3. A repeated geocode query hits the mocked geocoder **once** (cache works); a different query hits it again.
4. `registerPmtilesProtocol` is idempotent and a no-op when `pmtiles` is absent (light JS test or a documented
   manual check — the SDK has no JS test harness today; assert via a tiny Node script if cheap, otherwise
   document the manual verification).

## Risks & Mitigations

- **vi default breaks an app parsing English instructions** — accepted by operator; `?language=en` is the
  escape hatch, and this is a VN-only product.
- **Stale cache after re-import** — TTL=120s bounds it; a deploy restarts the process (empty cache). If a
  manual re-import without restart is a concern, document that the cache self-heals within 2 minutes.
- **Shared httpx client lifecycle** — `on_event` startup/shutdown is deprecated in newer FastAPI; acceptable
  at the pinned `fastapi==0.115.5`, or use the `lifespan` context manager if preferred during implementation.

## Definition of Done

- All success criteria met, adapter tests pass (`make adapter-test` or `pytest`).
- `requirements.txt` includes `cachetools` and `pytest`.
- Docs no longer reference Photon as the running geocoder or "4 endpoints"; terrain is documented.
- Demo light/dark toggle works against the live styles.
