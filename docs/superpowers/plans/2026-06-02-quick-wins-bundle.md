# Quick Wins Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first NullMaps upgrade sub-project — Vietnamese turn-by-turn, an in-process geocoder cache + shared HTTP client, a PMTiles SDK registrar, a gated `/metrics`, a demo dark toggle, a normalize-timeout cap, and docs truth-up.

**Architecture:** All adapter changes live in the single-worker FastAPI app (`services/adapter/app/main.py`); caching is in-process `cachetools.TTLCache`, HTTP uses one shared `httpx.AsyncClient` created in a `lifespan` handler. The SDK change is a dependency-free idempotent protocol registrar in `client/nullmaps.js`. The demo and docs are static edits. Tests follow the existing `importlib.reload` + `monkeypatch` + `TestClient` pattern.

**Tech Stack:** Python 3.12, FastAPI 0.115.5, httpx 0.28.1, cachetools, pytest; vanilla ESM JS; MapLibre GL JS 4.7.1.

**Spec:** `docs/superpowers/specs/2026-06-02-quick-wins-bundle-design.md`

**Branch:** `feat/quick-wins-bundle` (already created)

---

## File Structure

- **Modify** `services/adapter/requirements.txt` — add `cachetools`, `pytest`.
- **Modify** `services/adapter/app/main.py` — language param, lifespan + shared client, geocoder cache, `/metrics` key gate, normalize timeout param, docstring truth-up.
- **Modify** `services/adapter/tests/test_directions_matrix.py` — language + cache + metrics tests.
- **Modify** `client/nullmaps.js` — `registerPmtilesProtocol` registrar; call it in `map()` / `staticImage()`.
- **Create** `client/test-pmtiles-registrar.mjs` — Node verification script for the registrar.
- **Modify** `services/tiles/style/index.html` — `prefers-color-scheme` init + light/dark toggle button.
- **Modify** `services/tiles/README.md` — document `/style-dark.json`.
- **Modify** `docker-compose.yml` — purge the stale 503/Photon comment.
- **Modify** `README.md` — adapter surface, box-sizing (SQLite not Photon), terrain note.
- **Modify** `docs/architecture.md` — geocoder = SQLite; add normalizer/gateway/terrain.

---

## Task 1: Add dependencies

**Files:**
- Modify: `services/adapter/requirements.txt`

- [ ] **Step 1: Add cachetools + pytest**

Replace the full contents of `services/adapter/requirements.txt` with:

```
fastapi==0.115.5
uvicorn[standard]==0.32.1
httpx==0.28.1
cachetools==5.5.2
pytest==8.3.4
```

- [ ] **Step 2: Install and verify imports**

Run: `cd services/adapter && pip install -r requirements.txt && python -c "import cachetools, pytest; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add services/adapter/requirements.txt
git commit -m "build(adapter): add cachetools + pytest deps"
```

---

## Task 2: Vietnamese turn-by-turn (default vi-VN, override ?language=)

**Files:**
- Modify: `services/adapter/app/main.py` (add `LANG`/`language_for` near `costing_for` ~line 166; edit `directions()` payload ~line 246)
- Test: `services/adapter/tests/test_directions_matrix.py`

- [ ] **Step 1: Write the failing tests**

Append to `services/adapter/tests/test_directions_matrix.py`:

```python
def test_directions_default_language_is_vietnamese(monkeypatch):
    m = load()
    seen = {}

    async def capture(path, payload):
        seen.update(payload)
        return await fake_route(path, payload)

    monkeypatch.setattr(m, "valhalla", capture)
    c = TestClient(m.app)
    c.get("/maps/api/directions/json",
          params={"origin": "10.77,106.69", "destination": "10.79,106.72", "key": "secret"})
    assert seen["directions_options"]["language"] == "vi-VN"


def test_directions_language_override_en(monkeypatch):
    m = load()
    seen = {}

    async def capture(path, payload):
        seen.update(payload)
        return await fake_route(path, payload)

    monkeypatch.setattr(m, "valhalla", capture)
    c = TestClient(m.app)
    c.get("/maps/api/directions/json",
          params={"origin": "10.77,106.69", "destination": "10.79,106.72",
                  "language": "en", "key": "secret"})
    assert seen["directions_options"]["language"] == "en-US"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/adapter && python -m pytest tests/test_directions_matrix.py -k language -v`
Expected: FAIL with `KeyError: 'directions_options'`

- [ ] **Step 3: Add `language_for` helper**

In `services/adapter/app/main.py`, immediately after the `costing_for` function (ends ~line 168), add:

```python
# Google `language` / Goong -> Valhalla (Odin) locale. Default vi-VN for a VN-only
# product; unknown codes pass through and Odin falls back to en-US.
LANG = {"vi": "vi-VN", "vi-vn": "vi-VN", "en": "en-US", "en-us": "en-US"}


def language_for(request: Request) -> str:
    raw = request.query_params.get("language") or ""
    return LANG.get(raw.lower(), raw or "vi-VN")
```

- [ ] **Step 4: Attach `directions_options` to the route payload**

In `directions()`, find (~line 246):

```python
    payload = {"locations": locs, "costing": costing, "units": "kilometers"}
```

Replace with:

```python
    payload = {"locations": locs, "costing": costing, "units": "kilometers",
               "directions_options": {"language": language_for(request)}}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd services/adapter && python -m pytest tests/test_directions_matrix.py -k language -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Run the full adapter suite (no regressions)**

Run: `cd services/adapter && python -m pytest -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add services/adapter/app/main.py services/adapter/tests/test_directions_matrix.py
git commit -m "feat(adapter): Vietnamese turn-by-turn by default (override ?language=)"
```

---

## Task 3: Shared httpx client (lifespan) + in-process geocoder cache

**Files:**
- Modify: `services/adapter/app/main.py` (imports, lifespan, `valhalla`/`geocoder`/`maybe_normalize` ~lines 182-211)
- Test: `services/adapter/tests/test_directions_matrix.py`

- [ ] **Step 1: Write the failing cache test**

Append to `services/adapter/tests/test_directions_matrix.py`:

```python
def test_geocode_caches_repeated_query(monkeypatch):
    m = load()
    calls = {"n": 0}

    async def fake_fetch(path, params):
        calls["n"] += 1
        return {"results": [{"osm_id": "n1", "name": "Bến Thành", "kind": "poi",
                             "lat": 10.7704, "lon": 106.6951}]}

    monkeypatch.setattr(m, "_geocoder_fetch", fake_fetch)
    c = TestClient(m.app)
    p = {"address": "ben thanh", "key": "secret"}
    c.get("/maps/api/geocode/json", params=p)
    c.get("/maps/api/geocode/json", params=p)
    assert calls["n"] == 1  # second request served from cache

    c.get("/maps/api/geocode/json", params={"address": "cho lon", "key": "secret"})
    assert calls["n"] == 2  # different query -> a fresh fetch
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/adapter && python -m pytest tests/test_directions_matrix.py::test_geocode_caches_repeated_query -v`
Expected: FAIL with `AttributeError: ... has no attribute '_geocoder_fetch'`

- [ ] **Step 3: Add imports + TTLCache**

In `services/adapter/app/main.py`, update the import block (top of file) to add:

```python
from contextlib import asynccontextmanager
```

and

```python
from cachetools import TTLCache
```

(Add `from contextlib import asynccontextmanager` with the stdlib imports near `import os`/`import time`; add `from cachetools import TTLCache` with the third-party imports near `import httpx`.)

- [ ] **Step 4: Add the lifespan handler and wire it into FastAPI**

Immediately *before* the `app = FastAPI(` line (~line 30), insert:

```python
_geo_cache: TTLCache = TTLCache(maxsize=2048, ttl=120)  # geocoder read cache (2 min)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient()
    try:
        yield
    finally:
        await app.state.http.aclose()
```

Then change the FastAPI constructor call from:

```python
app = FastAPI(
    title="NullMaps API",
```

to:

```python
app = FastAPI(
    lifespan=lifespan,
    title="NullMaps API",
```

- [ ] **Step 5: Rewrite the engine helpers to use the shared client + cache**

Replace the three functions `valhalla`, `geocoder`, `maybe_normalize` (currently ~lines 182-211) with:

```python
async def valhalla(path: str, payload: dict) -> dict:
    try:
        r = await app.state.http.post(f"{VALHALLA_URL}{path}", json=payload, timeout=20)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"routing engine unreachable: {e}")
    return r.json() if r.content else {}


async def _geocoder_fetch(path: str, params: dict) -> dict:
    try:
        r = await app.state.http.get(f"{GEOCODER_URL}{path}", params=params, timeout=10)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"geocoder unreachable: {e}")
    return r.json() if r.content else {}


async def geocoder(path: str, params: dict) -> dict:
    """Cached geocoder read. Typeahead/reverse repeat heavily; cache the engine
    response for `ttl` seconds. Errors raise from _geocoder_fetch and are not cached."""
    key = (path, frozenset(params.items()))
    if key in _geo_cache:
        return _geo_cache[key]
    result = await _geocoder_fetch(path, params)
    _geo_cache[key] = result
    return result


async def maybe_normalize(request: Request, text: str, timeout: float = 8) -> str:
    """Optional Phase-5 AI cleanup, opt-in via ?normalize=1. Fail-open: any error,
    timeout, or unconfigured normalizer returns the input unchanged."""
    flag = (request.query_params.get("normalize") or "").lower()
    if not NORMALIZER_URL or flag not in ("1", "true", "yes"):
        return text
    try:
        r = await app.state.http.get(f"{NORMALIZER_URL}/normalize",
                                     params={"q": text}, timeout=timeout)
        return (r.json().get("normalized") or text) if r.content else text
    except httpx.HTTPError:
        return text
```

- [ ] **Step 6: Run the cache test + full suite**

Run: `cd services/adapter && python -m pytest -q`
Expected: all pass (including `test_geocode_caches_repeated_query`)

- [ ] **Step 7: Commit**

```bash
git add services/adapter/app/main.py services/adapter/tests/test_directions_matrix.py
git commit -m "perf(adapter): shared httpx client (lifespan) + in-process geocoder TTL cache"
```

---

## Task 4: Cap the normalize timeout on the autocomplete path

**Files:**
- Modify: `services/adapter/app/main.py` (`autocomplete()` ~line 506; `geocode()` ~line 441)

- [ ] **Step 1: Pass a short timeout from autocomplete, explicit 8s from geocode**

In `autocomplete()`, find:

```python
    text = await maybe_normalize(request, text)
```

Replace with:

```python
    text = await maybe_normalize(request, text, timeout=2)  # typeahead must not hang on the LLM
```

In `geocode()` (forward branch), find:

```python
        address = await maybe_normalize(request, address)
```

Replace with:

```python
        address = await maybe_normalize(request, address, timeout=8)
```

- [ ] **Step 2: Run the full suite (no regressions; default path unchanged)**

Run: `cd services/adapter && python -m pytest -q`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add services/adapter/app/main.py
git commit -m "fix(adapter): cap autocomplete normalize timeout at 2s (fail-open)"
```

---

## Task 5: Gate /metrics behind the API key

**Files:**
- Modify: `services/adapter/app/main.py` (`metrics()` ~lines 145-146)
- Test: `services/adapter/tests/test_directions_matrix.py`

- [ ] **Step 1: Write the failing test**

Append to `services/adapter/tests/test_directions_matrix.py`:

```python
def test_metrics_requires_key():
    m = load()
    c = TestClient(m.app)
    assert c.get("/metrics").status_code == 403
    assert c.get("/metrics", params={"key": "secret"}).status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/adapter && python -m pytest tests/test_directions_matrix.py::test_metrics_requires_key -v`
Expected: FAIL (first assert: `/metrics` returns 200, not 403)

- [ ] **Step 3: Add the key check**

In `services/adapter/app/main.py`, change:

```python
@app.get("/metrics")
def metrics():
    """Prometheus text format — scrape from Grafana/Prometheus (gateway gates it)."""
```

to:

```python
@app.get("/metrics")
def metrics(request: Request):
    """Prometheus text format — scrape from Grafana/Prometheus. Key-gated (pass
    ?key= or X-API-Key) in addition to the gateway, so it can't leak if bypassed."""
    require_key(request)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/adapter && python -m pytest tests/test_directions_matrix.py::test_metrics_requires_key -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `cd services/adapter && python -m pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add services/adapter/app/main.py services/adapter/tests/test_directions_matrix.py
git commit -m "fix(adapter): require API key on /metrics (defense in depth)"
```

---

## Task 6: PMTiles registrar in the SDK

**Files:**
- Modify: `client/nullmaps.js` (add registrar; call in `map()` ~line 58 and `staticImage()` ~line 133)
- Create: `client/test-pmtiles-registrar.mjs`

- [ ] **Step 1: Write the failing Node verification script**

Create `client/test-pmtiles-registrar.mjs`:

```js
// Verifies the PMTiles registrar: no-op without the lib, idempotent with it.
import { NullMaps } from "./nullmaps.js";

let calls = 0;
const fakeMaplibre = { addProtocol: () => { calls++; } };

// No global pmtiles available -> must NOT register.
NullMaps.registerPmtilesProtocol(fakeMaplibre);
if (calls !== 0) { console.error("FAIL: registered without pmtiles present"); process.exit(1); }

// Provide a fake pmtiles lib -> registers exactly once, even if called twice.
globalThis.pmtiles = { Protocol: function () { this.tile = () => {}; } };
NullMaps.registerPmtilesProtocol(fakeMaplibre);
NullMaps.registerPmtilesProtocol(fakeMaplibre);
if (calls !== 1) { console.error(`FAIL: expected 1 registration, got ${calls}`); process.exit(1); }

console.log("OK: pmtiles registrar is idempotent and no-ops without the lib");
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd client && node test-pmtiles-registrar.mjs`
Expected: FAIL — `TypeError: NullMaps.registerPmtilesProtocol is not a function`

- [ ] **Step 3: Add the registrar to `client/nullmaps.js`**

At the top of `client/nullmaps.js`, *before* `export class NullMaps {` (after the header comment), add:

```js
let _pmtilesRegistered = false;

// Register the MapLibre `pmtiles://` protocol once. No-op if already registered or
// if the pmtiles lib isn't available. Pass the pmtiles module explicitly, or rely on
// a global `pmtiles` (UMD build). CLAUDE.md: MapLibre fails silently without this.
function registerPmtilesProtocol(maplibregl, pmtiles) {
  if (_pmtilesRegistered) return;
  const lib = pmtiles || (typeof globalThis !== "undefined" ? globalThis.pmtiles : undefined);
  if (!lib || !maplibregl || typeof maplibregl.addProtocol !== "function") return;
  maplibregl.addProtocol("pmtiles", new lib.Protocol().tile);
  _pmtilesRegistered = true;
}
```

At the end of the file, *before* `export default NullMaps;`, add:

```js
NullMaps.registerPmtilesProtocol = registerPmtilesProtocol;
export { registerPmtilesProtocol };
```

- [ ] **Step 4: Call the registrar in `map()`**

In `map()`, change the destructure line:

```js
    const { theme = "light", controls = true, ...mapOpts } = opts;
```

to:

```js
    const { theme = "light", controls = true, pmtiles, ...mapOpts } = opts;
    registerPmtilesProtocol(maplibregl, pmtiles);
```

- [ ] **Step 5: Call the registrar in `staticImage()`**

In `staticImage()`, change the destructure line:

```js
    const { center = [106.700, 10.776], zoom = 12, size = [600, 400], theme = "light",
      markers = [], route = null, pitch = 0, bearing = 0 } = opts;
```

to:

```js
    const { center = [106.700, 10.776], zoom = 12, size = [600, 400], theme = "light",
      markers = [], route = null, pitch = 0, bearing = 0, pmtiles } = opts;
    registerPmtilesProtocol(maplibregl, pmtiles);
```

- [ ] **Step 6: Run the verification script to verify it passes**

Run: `cd client && node test-pmtiles-registrar.mjs`
Expected: `OK: pmtiles registrar is idempotent and no-ops without the lib`

- [ ] **Step 7: Commit**

```bash
git add client/nullmaps.js client/test-pmtiles-registrar.mjs
git commit -m "feat(sdk): idempotent PMTiles protocol registrar in map()/staticImage()"
```

---

## Task 7: Demo light/dark toggle

**Files:**
- Modify: `services/tiles/style/index.html`
- Modify: `services/tiles/README.md`

- [ ] **Step 1: Add a toggle button + theme styling**

In `services/tiles/style/index.html`, inside the `<style>` block, after the `#badge { ... }` rule, add:

```css
    #theme-toggle {
      position: absolute; top: 10px; right: 10px; z-index: 1;
      font-size: 16px; line-height: 1; padding: 6px 9px; cursor: pointer;
      background: #163300; color: #9fe870; border: none; border-radius: 6px;
    }
```

After the `<div id="badge">...</div>` line, add:

```html
  <button id="theme-toggle" title="Toggle light / dark">🌓</button>
```

- [ ] **Step 2: Drive the style from prefers-color-scheme + the toggle**

Replace the entire `<script> ... </script>` block (currently lines ~22-36) with:

```html
  <script>
    // PMTiles protocol (harmless when tiles come via Martin) — registered so a
    // pmtiles:// source also works if you serve the .pmtiles file directly.
    maplibregl.addProtocol("pmtiles", new pmtiles.Protocol().tile);

    const styleUrl = (t) => (t === "dark" ? "/style-dark.json" : "/style.json");
    let theme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";

    // Full style (OMT layers + Hoàng Sa / Trường Sa labels), tiles + glyphs
    // both served by Martin behind Caddy /tiles — single origin, self-hosted.
    const map = new maplibregl.Map({
      container: "map",
      center: [106.700, 10.776],   // Ho Chi Minh City (lon, lat)
      zoom: 11,
      style: styleUrl(theme)
    });
    map.addControl(new maplibregl.NavigationControl());

    // Controls persist across setStyle (they're not part of the style document).
    document.getElementById("theme-toggle").addEventListener("click", () => {
      theme = theme === "dark" ? "light" : "dark";
      map.setStyle(styleUrl(theme));
    });
  </script>
```

- [ ] **Step 3: Document /style-dark.json**

In `services/tiles/README.md`, find the line:

```
- `style/index.html` — demo page (HCMC) that loads `/style.json`.
```

Replace with:

```
- `style/index.html` — demo page (HCMC); honors `prefers-color-scheme` and has a light/dark toggle.
- `style/style-dark.json` — dark MapLibre style, served at `/style-dark.json` (first-class endpoint).
```

- [ ] **Step 4: Verify visually**

Run: `make demo`
Open the printed URL, confirm the basemap renders, click the 🌓 button, confirm it switches between light and dark, and that the navigation control survives the swap. Stop with `Ctrl-C` (or `make down`).

- [ ] **Step 5: Commit**

```bash
git add services/tiles/style/index.html services/tiles/README.md
git commit -m "feat(tiles): demo honors prefers-color-scheme + light/dark toggle"
```

---

## Task 8: Docs truth-up (adapter surface, SQLite geocoder, terrain)

**Files:**
- Modify: `services/adapter/app/main.py` (module docstring lines 7-16)
- Modify: `docker-compose.yml` (comment line 100-101)
- Modify: `README.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Fix the adapter module docstring**

In `services/adapter/app/main.py`, replace the docstring block:

```python
Live now (Valhalla is up):
  GET /maps/api/directions/json        -> Valhalla /route
  GET /maps/api/distancematrix/json    -> Valhalla /sources_to_targets

Pending Phase 3 (Photon) — return a clear 503 until geocoding is online:
  GET /maps/api/geocode/json
  GET /maps/api/place/autocomplete/json
```

with:

```python
Live endpoints (all engines up):
  GET /maps/api/directions/json        -> Valhalla /route (or /optimized_route)
  GET /maps/api/distancematrix/json    -> Valhalla /sources_to_targets
  GET /maps/api/geocode/json           -> geocoder /geocode | /reverse
  GET /maps/api/place/autocomplete/json-> geocoder /autocomplete
  GET /maps/api/place/nearbysearch/json-> geocoder /nearby
  GET /maps/api/place/details/json     -> geocoder /detail
  GET /v1/isochrone, GET /v1/snap      -> Valhalla /isochrone, /trace_route (native)
```

- [ ] **Step 2: Fix the docker-compose adapter comment**

In `docker-compose.yml`, replace:

```yaml
  # --- Phase 4: Google/Goong-compat FastAPI adapter ------------------------
  # Live: /maps/api/directions/json, /maps/api/distancematrix/json -> Valhalla.
  # Pending Phase 3: geocode + place/autocomplete return 503 until Photon is up.
```

with:

```yaml
  # --- Phase 4: Google/Goong-compat FastAPI adapter ------------------------
  # Live: directions + distancematrix -> Valhalla; geocode/reverse/autocomplete/
  # nearby/details -> geocoder; /v1/isochrone + /v1/snap native. All engines up.
```

- [ ] **Step 3: Fix README adapter section + roadmap**

In `README.md`, replace:

```
make adapter-test                 # Directions + Matrix (live), geocode 503 (pending Phase 3)
```

with:

```
make adapter-test                 # Directions + Matrix + Geocode/Autocomplete (all live)
```

Replace the roadmap Phase 4 status cell:

```
| 4 | Google/Goong-compat API (**required**) | FastAPI adapter | **done** (all 4 endpoints live) |
```

with:

```
| 4 | Google/Goong-compat API (**required**) | FastAPI adapter | **done** (directions, matrix, geocode/reverse, autocomplete, nearby, details + isochrone/snap) |
```

- [ ] **Step 4: Fix README box-sizing (SQLite, not Photon) + add terrain note**

In `README.md`, replace the box-sizing row:

```
| + Phase 3 Photon/ES | +several GB | + ES index | hungriest component |
```

with:

```
| + Phase 3 geocoder (SQLite) | ~256 MB | ~56 MB index | lightweight; Photon = heavier prod swap-in |
```

Then, directly under the Phase 1 quickstart code block (after the line `\`make help\` lists all commands. Full steps: ...`), add a new paragraph:

```
**Terrain overlays:** hillshade + 100 m contour lines (from the Copernicus GLO-90 DEM) build via
`infra/build-hillshade.sh` / `infra/build-contour.sh` and serve through Martin alongside the basemap.
```

- [ ] **Step 5: Fix architecture.md geocoder + stack table**

In `docs/architecture.md`, in the component diagram replace the two occurrences `│ Photon │` / `│ (P3)   │` / `│ geocode│` labels so the box reads `geocoder` / `(P3)` / `SQLite`. Concretely, replace these three diagram lines:

```
                  │ Valhalla (P2) │  │ Photon │  Martin (P1)
                  │ route/matrix  │  │ (P3)   │  ┌────────────┐
                  │ motorbike     │  │ geocode│  │ vietnam.   │
```

with:

```
                  │ Valhalla (P2) │  │geocoder│  Martin (P1)
                  │ route/matrix  │  │ (P3)   │  ┌────────────┐
                  │ motorbike     │  │ SQLite │  │ vietnam.   │
```

Replace the Geocoding stack row:

```
| Geocoding | Photon                          | Typeahead + diacritic folding |
```

with:

```
| Geocoding | lightweight SQLite (FTS5/R*Tree) | Typeahead + diacritic folding; Photon = prod swap-in |
```

Add these rows to the stack table, after the `Adapter` row:

```
| Gateway   | Caddy (`:8088`, key-gated front door) | Single entrypoint; engines have no public ports |
| AI helper | LiteLLM → Qwen (optional, Phase 5) | Vietnamese address normalization, opt-in `?normalize=1` |
| Terrain   | Copernicus GLO-90 DEM → GDAL/tippecanoe | Hillshade + contour overlays via Martin |
```

- [ ] **Step 6: Verify no stale references remain**

Run:
```bash
cd /Users/nullshift-labs/dev/nullmap
grep -rn -i "all 4 endpoints\|return a clear 503\|return 503 until Photon\|Photon/ES" README.md docs/architecture.md services/adapter/app/main.py docker-compose.yml
```
Expected: no matches (exit non-zero / empty output).

- [ ] **Step 7: Commit**

```bash
git add services/adapter/app/main.py docker-compose.yml README.md docs/architecture.md
git commit -m "docs: truth-up adapter surface, SQLite geocoder, terrain"
```

---

## Task 9: Final verification

- [ ] **Step 1: Full adapter test suite**

Run: `cd services/adapter && python -m pytest -q`
Expected: all pass.

- [ ] **Step 2: SDK registrar check**

Run: `cd client && node test-pmtiles-registrar.mjs`
Expected: `OK: ...`

- [ ] **Step 3: Compose still validates (CI parity)**

Run: `cd /Users/nullshift-labs/dev/nullmap && docker compose -f docker-compose.yml config -q`
Expected: no output, exit 0.

- [ ] **Step 4: Confirm the branch log**

Run: `git log --oneline main..HEAD`
Expected: the eight feature/doc commits from Tasks 1-8.

---

## Self-Review (completed by plan author)

- **Spec coverage:** vi turn-by-turn (T2) ✓ · shared httpx + cache (T3) ✓ · normalize timeout cap (T4) ✓ · `/metrics` gate (T5) ✓ · PMTiles registrar (T6) ✓ · dark toggle + `/style-dark.json` doc (T7) ✓ · docs truth-up incl. terrain (T8) ✓ · cachetools + pytest deps (T1) ✓. All spec success criteria mapped.
- **Placeholder scan:** none — every code/edit step shows exact content and exact find/replace anchors.
- **Type/name consistency:** `_geocoder_fetch` (defined T3) is the monkeypatch target in the T3 test; `geocoder()` keeps its signature so existing tests that patch `m.geocoder` still bypass the cache; `language_for`/`LANG` defined once in T2; `registerPmtilesProtocol` defined once in T6 and referenced by both `map()` and `staticImage()` and the Node test.
