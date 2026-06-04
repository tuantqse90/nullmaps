"""NullMaps Google/Goong-compat adapter (Phase 4).

Maps Google Maps API request/response shapes onto the native NullMaps engines so
existing apps can repoint without rewriting client code. Goong's REST shapes mirror
Google's closely, so this Google-compatible surface covers most of Goong too.

Live endpoints (all engines up):
  GET /maps/api/directions/json        -> Valhalla /route (or /optimized_route)
  GET /maps/api/distancematrix/json    -> Valhalla /sources_to_targets
  GET /maps/api/geocode/json           -> geocoder /geocode | /reverse
  GET /maps/api/place/autocomplete/json-> geocoder /autocomplete
  GET /maps/api/place/nearbysearch/json-> geocoder /nearby
  GET /maps/api/place/details/json     -> geocoder /detail
  GET /v1/isochrone, GET /v1/snap      -> Valhalla /isochrone, /trace_route (native)

Auth: single shared API_KEY, checked on every endpoint except /healthz.
Pass it Google-style as ?key=... or as an X-API-Key header.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager

import httpx
from cachetools import TTLCache, LRUCache
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .polyline import reencode, encode, decode
from .vinarrative import vi_instruction

_geo_cache: TTLCache = TTLCache(maxsize=2048, ttl=120)  # geocoder read cache (2 min)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=None)  # per-call timeouts are explicit; avoid httpx's hidden 5s default
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(
    lifespan=lifespan,
    title="NullMaps API",
    version="1.0.0",
    description=(
        "Self-hosted, Google/Goong-compatible maps API. Authenticate with the shared "
        "key via `?key=...` or header `X-API-Key`.\n\n"
        "**Directions** `GET /maps/api/directions/json` — `origin`, `destination` (lat,lng), "
        "optional `waypoints` (`optimize:true|lat,lng|...` for TSP), `mode`/`vehicle` "
        "(motorbike by default), returns Google shape incl. turn-by-turn `steps`.\n\n"
        "**Distance Matrix** `GET /maps/api/distancematrix/json` — `origins`, `destinations` "
        "(`|`-separated lat,lng).\n\n"
        "**Geocoding** `GET /maps/api/geocode/json` — `address=` (forward) or `latlng=` (reverse); "
        "optional `location=lat,lng` viewport bias, `normalize=1` for AI cleanup.\n\n"
        "**Autocomplete** `GET /maps/api/place/autocomplete/json` — `input=`, optional `location=`.\n\n"
        "**Fleet (native):** `GET /v1/isochrone?location=&contours=10,20`, "
        "`GET /v1/snap?path=lat,lng|...`."
    ),
)

# Google clients branch on a `status` field. Render HTTPException on the customer
# surface (/maps, /v1) as a Google-shaped body while preserving the HTTP status code.
_GOOGLE_STATUS = {400: "INVALID_REQUEST", 403: "REQUEST_DENIED", 404: "NOT_FOUND",
                  429: "OVER_QUERY_LIMIT", 502: "UNKNOWN_ERROR"}


@app.exception_handler(StarletteHTTPException)
async def google_shaped_error(request: Request, exc: StarletteHTTPException):
    path = request.url.path
    if path.startswith("/maps") or path.startswith("/v1"):
        status = _GOOGLE_STATUS.get(exc.status_code, "UNKNOWN_ERROR")
        return JSONResponse({"status": status, "error_message": exc.detail},
                            status_code=exc.status_code)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


API_KEY = os.environ.get("API_KEY", "")
VALHALLA_URL = os.environ.get("VALHALLA_URL", "http://valhalla:8002").rstrip("/")
GEOCODER_URL = os.environ.get("GEOCODER_URL", "http://geocoder:2322").rstrip("/")
PHOTON_URL = os.environ.get("PHOTON_URL", "http://photon:2322").rstrip("/")
# Text-search engine: "photon" (prominence-ranked, falls back to the SQLite geocoder
# on error/empty) or "sqlite" (the lightweight engine only). nearby/details always
# use the SQLite geocoder (Photon has no category-nearby / by-id lookup here).
SEARCH_ENGINE = os.environ.get("SEARCH_ENGINE", "photon").lower()
NORMALIZER_URL = os.environ.get("NORMALIZER_URL", "").rstrip("/")

# Google travel modes / Goong vehicle -> Valhalla costing. Motorbike-first: an
# unspecified or two-wheeler mode routes as a scooter (NullMaps' primary use case).
COSTING = {
    None: "motor_scooter",
    "": "motor_scooter",
    "two_wheeler": "motor_scooter",
    "motorcycle": "motorcycle",
    "motorbike": "motor_scooter",
    "bike": "motor_scooter",
    "scooter": "motor_scooter",
    "driving": "auto",
    "car": "auto",
    "walking": "pedestrian",
    "bicycling": "bicycle",
    "bicycle": "bicycle",
    "truck": "truck",
    "hgv": "truck",
    "lorry": "truck",
}


_USE_KNOBS = ("use_ferry", "use_tolls", "use_highways", "use_living_streets")


def costing_options(request: Request, costing: str) -> dict:
    """Per-costing options forwarded into Valhalla costing_options[costing].
    General knobs (use_*/top_speed) apply to any costing — Valhalla ignores ones
    that don't apply — and trucks additionally get dimensions/limits (logistics)."""
    q = request.query_params
    opts: dict = {}
    for k in _USE_KNOBS:
        f = _ffloat(q.get(k))
        if f is not None:
            opts[k] = max(0.0, min(1.0, f))              # Valhalla use_* are 0..1
    ts = _ffloat(q.get("top_speed"))
    if ts is not None:
        opts["top_speed"] = ts
    if costing == "truck":
        for param in ("height", "width", "length", "weight", "axle_load"):
            f = _ffloat(q.get(param))
            if f is not None:
                opts[param] = f
        if (q.get("hazmat") or "").lower() in ("1", "true", "yes"):
            opts["hazmat"] = True
    return {costing: opts} if opts else {}


def avoid_locations(request: Request) -> list:
    """Google-ish `avoid=lat,lng|lat,lng` -> Valhalla exclude_locations."""
    out = []
    for s in (request.query_params.get("avoid") or "").split("|"):
        s = s.strip()
        if "," in s:
            try:
                out.append(parse_latlng(s))
            except HTTPException:
                pass
    return out


def avoid_zones(request: Request) -> list:
    """?avoid_zones=<GeoJSON Polygon|MultiPolygon> -> Valhalla exclude_polygons
    (a list of rings, each a list of [lon, lat]). Fail-open on malformed input.

    Cap on the TOTAL VERTEX COUNT (what valhalla.json max_exclude_polygons_length
    actually limits), not on the raw string length — a small high-precision polygon
    can exceed a char cap while being well within Valhalla's point budget, and a
    char cap would silently drop the zone (route runs straight through it). A loose
    1 MB raw guard still prevents parsing pathological input."""
    raw = request.query_params.get("avoid_zones")
    if not raw or len(raw) > 1_000_000:
        return []
    try:
        geo = json.loads(raw)
        if not isinstance(geo, dict):                # e.g. "[1,2,3]" or a bare number
            return []
        t = geo.get("type")
        if t == "Polygon":
            rings = [geo["coordinates"][0]]
        elif t == "MultiPolygon":
            rings = [poly[0] for poly in geo["coordinates"]]
        else:
            return []
        if sum(len(ring) for ring in rings) > 10000:   # Valhalla max_exclude_polygons_length
            return []
        return [[[float(pt[0]), float(pt[1])] for pt in ring] for ring in rings]
    except (ValueError, KeyError, TypeError, IndexError):
        return []


def snap_radius(request: Request) -> int:
    """Per-location snap search radius (m). Lets borderline points snap to a road."""
    try:
        r = int(request.query_params.get("snap_radius") or 50)
    except ValueError:
        r = 50
    return max(0, min(r, 200))


def with_radius(locs: list, radius: int) -> list:
    """Return a copy of each location with a snap `radius` set."""
    return [{**loc, "radius": radius} for loc in locs]


def with_road_snap(locs: list, radius: int = 700,
                   min_road_class: str = "residential") -> list:
    """Snap copy that also excludes service/track edges via `search_filter`.

    A point can snap to an edge with no path to the rest of the network: a
    disconnected service-road island (airport airfield centroid / gated complex),
    or it can sit deep inside a large named polygon (lake, park, campus) whose
    geocoded centroid is far from any road. Excluding `service`/`track` AND using a
    generous search radius forces the snap onto the nearest connected public road,
    so routing succeeds. Used only as a last-resort retry after the normal
    (nearest-edge) attempts return "No path".
    """
    return [{**loc, "radius": radius,
             "search_filter": {"min_road_class": min_road_class}} for loc in locs]


def require_key(request: Request) -> None:
    """One shared key. Accept ?key= (Google style) or X-API-Key header."""
    supplied = request.query_params.get("key") or request.headers.get("x-api-key")
    if not API_KEY or supplied != API_KEY:
        raise HTTPException(status_code=403, detail="invalid API key")


# --- usage metrics + simple per-key rate limit (single uvicorn worker) -------
RATE_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "600"))
_counts: dict = defaultdict(int)              # (endpoint, status) -> total (finite key space)
_by_key: LRUCache = LRUCache(maxsize=1024)    # key -> total requests (bounded)
_rl: TTLCache = TTLCache(maxsize=1024, ttl=120)  # key -> [minute_window, count] (bounded)


@app.middleware("http")
async def metrics_and_ratelimit(request: Request, call_next):
    path = request.url.path
    metered = path.startswith("/maps") or path.startswith("/v1")
    if metered:
        key = request.query_params.get("key") or request.headers.get("x-api-key") or "anon"
        minute = int(time.time() // 60)
        st = _rl.get(key)
        if st is None or st[0] != minute:
            st = [minute, 0]
        st[1] += 1
        _rl[key] = st
        _by_key[key] = _by_key.get(key, 0) + 1
        if st[1] > RATE_PER_MIN:
            _counts[(path, 429)] += 1
            return JSONResponse(
                {"status": "OVER_QUERY_LIMIT", "error_message": f"rate limit {RATE_PER_MIN}/min"},
                status_code=429)
    resp = await call_next(request)
    ep = (request.scope.get("route").path if request.scope.get("route") else path)
    _counts[(ep, resp.status_code)] += 1
    return resp


@app.get("/metrics")
def metrics(request: Request):
    """Prometheus text format — scrape from Grafana/Prometheus. Key-gated (pass
    ?key= or X-API-Key) in addition to the gateway, so it can't leak if bypassed."""
    require_key(request)
    out = ["# TYPE nullmaps_requests_total counter"]
    for (ep, st), n in sorted(_counts.items()):
        out.append(f'nullmaps_requests_total{{endpoint="{ep}",status="{st}"}} {n}')
    out.append("# TYPE nullmaps_requests_by_key_total counter")
    for k, n in sorted(_by_key.items()):
        out.append(f'nullmaps_requests_by_key_total{{key="{k[:8]}"}} {n}')
    return PlainTextResponse("\n".join(out) + "\n")


def parse_latlng(s: str) -> dict:
    """'10.77,106.70' -> {'lat':10.77,'lon':106.70}. Raises 400 on bad input.

    Rejects non-finite (nan/inf, incl. overflow literals like '1e500') and
    out-of-range values so they never reach Valhalla as non-JSON NaN/Infinity
    (which would 500/502 instead of a clean 400)."""
    try:
        lat, lon = (float(x) for x in s.split(",", 1))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail=f"bad lat,lng: {s!r}")
    if not (math.isfinite(lat) and math.isfinite(lon)) or \
            not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise HTTPException(status_code=400, detail=f"lat,lng out of range: {s!r}")
    return {"lat": lat, "lon": lon}


def _ffloat(v) -> float | None:
    """float() that rejects nan/inf (which are not JSON and 500 Valhalla)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _clean_text(s: str | None) -> str | None:
    """Strip control chars (incl. NUL) that break the downstream FTS/HTTP layer,
    keeping printable text + normal whitespace (Vietnamese, emoji, etc. survive)."""
    if s is None:
        return s
    return "".join(ch for ch in s if ch.isprintable() or ch in " \t")


def costing_for(request: Request) -> str:
    mode = request.query_params.get("mode") or request.query_params.get("vehicle")
    return COSTING.get((mode or "").lower(), "motor_scooter")


# Google `language` / Goong -> Valhalla (Odin) locale. Default vi-VN for a VN-only
# product; unknown codes pass through and Odin falls back to en-US.
LANG = {"vi": "vi-VN", "vi-vn": "vi-VN", "en": "en-US", "en-us": "en-US"}


def language_for(request: Request) -> str:
    raw = request.query_params.get("language") or ""
    return LANG.get(raw.lower(), raw or "vi-VN")


def dist_text(meters: float) -> str:
    return f"{meters/1000:.1f} km" if meters >= 1000 else f"{round(meters)} m"


def dur_text(seconds: float) -> str:
    m = round(seconds / 60)
    if m < 60:
        return f"{m} min"
    return f"{m//60} h {m%60} min"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


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


# --- Photon (prominence-ranked typeahead) -> internal result shape ------------
_PLACE_TYPES = {"city", "town", "village", "hamlet", "district", "locality", "state",
                "county", "suburb", "neighbourhood", "quarter", "region", "island"}


def _photon_kind(p: dict) -> str:
    t = p.get("type") or ""
    if t == "house" or p.get("housenumber"):
        return "address"
    if p.get("osm_key") == "highway" or t == "street":
        return "street"
    if t in _PLACE_TYPES:
        return "place"
    return "poi"


def _photon_feature(f: dict) -> dict:
    """Map one Photon GeoJSON feature onto the SQLite-geocoder result dict the adapter's
    formatters expect (name/lat/lon/kind/category/housenumber/street/city/district/
    region/osm_id/extra). `extra` carries the district/city context shown as the
    autocomplete secondary line."""
    p = f.get("properties") or {}
    c = (f.get("geometry") or {}).get("coordinates") or [None, None]
    name = p.get("name") or " ".join(filter(None, [p.get("housenumber"), p.get("street")])) \
        or p.get("city") or p.get("district") or ""
    # secondary context: street (distinguishes branches in the same ward) + district + city
    ctx_src = (p.get("street") if p.get("name") else None, p.get("district"), p.get("city"))
    ctx = ", ".join(dict.fromkeys(x for x in ctx_src if x))
    return {
        "name": name, "lat": c[1], "lon": c[0],
        "kind": _photon_kind(p), "category": p.get("osm_value") or "",
        "housenumber": p.get("housenumber"), "street": p.get("street"),
        "city": p.get("city"), "district": p.get("district"), "region": p.get("state"),
        "osm_id": f"{p.get('osm_type', '')}{p.get('osm_id', '')}", "extra": ctx,
    }


_HN_RE = re.compile(r"^\s*(\d{1,5}[A-Za-z]?(?:/\d+)?)\s+(\D.{2,})$")
_DISTRICT_Q_RE = re.compile(r"^\s*(?:q|quận|quan)\.?\s*([1-9]|1[0-2])\s*$", re.IGNORECASE)


def _is_district_q(q) -> bool:
    """A bare HCMC district shorthand (q7 / quận 7). Photon returns POIs named 'Q7';
    the SQLite geocoder resolves it to the legacy district (districts were abolished in
    the 2025 admin reform, so it's the only source)."""
    return bool(q and _DISTRICT_Q_RE.match(q))


def _split_housenumber(q: str):
    """'543 Nguyễn Duy Trinh' -> ('543', 'Nguyễn Duy Trinh'); else (None, q).

    Photon free-text search mishandles a leading house number — it matches '543' as a
    house number on ANY street with that number ('543 Nguyễn Hoàng Tôn' in Hà Nội) or
    fuzzes the digits near the viewport. Searching the street alone ranks it correctly;
    we re-attach the number to plain street results (Google-style '543 <street>')."""
    m = _HN_RE.match(q or "")
    return (m.group(1), m.group(2).strip()) if m else (None, q)


async def photon_call(path: str, params: dict) -> dict | None:
    """Run a geocoder-style call (/geocode, /autocomplete, /reverse) against Photon."""
    lat, lon = params.get("lat"), params.get("lon")
    if path == "/reverse":
        if lat is None or lon is None:
            return None
        r = await app.state.http.get(f"{PHOTON_URL}/reverse", params={"lat": lat, "lon": lon, "limit": 1}, timeout=8)
        r.raise_for_status()
        feats = (r.json() or {}).get("features") or []
        return {"result": _photon_feature(feats[0]) if feats else None}
    q = params.get("q")
    if not q:
        return {"results": []}
    want = params.get("limit", 5)
    hn, street = _split_housenumber(q)          # search the street alone; re-attach hn below
    pp = {"q": street, "limit": want * 2 + 2}   # over-fetch so dedup still fills `want`
    if lat is not None and lon is not None:
        pp["lat"], pp["lon"] = lat, lon
    r = await app.state.http.get(f"{PHOTON_URL}/api", params=pp, timeout=8)
    r.raise_for_status()
    results = [_photon_feature(f) for f in ((r.json() or {}).get("features") or [])]
    if hn:
        for x in results:                       # '543 Đường Nguyễn Duy Trinh' on a plain street
            if x["kind"] == "street" and not any(c.isdigit() for c in (x["name"] or "")):
                x["name"] = f"{hn} {x['name']}"   # baked into name; don't also set housenumber
    # drop predictions the user can't tell apart (same name + same context line)
    seen, deduped = set(), []
    for x in results:
        k = (x["name"], x["extra"])
        if k not in seen:
            seen.add(k)
            deduped.append(x)
    return {"results": deduped[:want]}


async def geocoder(path: str, params: dict) -> dict:
    """Cached geocoder read. Typeahead/reverse repeat heavily; cache the engine
    response for `ttl` seconds. Text search prefers Photon (SEARCH_ENGINE=photon) and
    falls back to the SQLite geocoder on error/empty; nearby/details stay on SQLite."""
    key = (path, frozenset(params.items()))
    if key in _geo_cache:
        return _geo_cache[key]
    if SEARCH_ENGINE == "photon" and path in ("/geocode", "/autocomplete", "/reverse") \
            and not _is_district_q(params.get("q")):     # q1-q12 -> SQLite legacy districts
        try:
            res = await photon_call(path, params)
        except Exception:
            res = None                                   # Photon down/slow -> SQLite below
        if path == "/reverse":
            if res and res.get("result"):
                _geo_cache[key] = res
                return res
        else:
            p_items = (res or {}).get("results") or []
            # Typo net: Photon fuzz is weak on genuine misspellings ('nguyn hue'). If a
            # multi-word query yields ≤1 hit it likely whiffed — pull the SQLite trigram
            # results (strong on typos) in front, deduped.
            q = params.get("q") or ""
            if len(q.split()) >= 2 and len(p_items) <= 1:
                try:
                    s_items = (await _geocoder_fetch(path, params)).get("results") or []
                except Exception:
                    s_items = []
                if s_items:
                    keyf = lambda x: (x.get("name"), round(x.get("lat") or 0, 4))
                    seen = {keyf(x) for x in s_items}
                    p_items = s_items + [x for x in p_items if keyf(x) not in seen]
            if p_items:
                res = {"results": p_items[:params.get("limit", 5)]}
                _geo_cache[key] = res
                return res
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


@app.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "service": "nullmaps-adapter",
        "phase": 4,
        "live": ["directions", "distancematrix", "geocode", "place/autocomplete",
                 "place/nearbysearch", "place/details"],
        "native": ["v1/isochrone", "v1/snap", "directions optimize:true"],
        "pending": [],
    }


@app.get("/maps/api/directions/json")
async def directions(request: Request):
    """Google Directions shape. `waypoints=optimize:true|lat,lng|...` reorders the
    intermediate stops (Valhalla /optimized_route = TSP) and returns waypoint_order."""
    require_key(request)
    origin = request.query_params.get("origin")
    destination = request.query_params.get("destination")
    if not origin or not destination:
        return JSONResponse({"status": "INVALID_REQUEST", "routes": []}, status_code=400)

    segs = [s for s in (request.query_params.get("waypoints") or "").split("|") if s]
    optimize = False
    if segs and segs[0].startswith("optimize:"):
        optimize = segs[0].split(":", 1)[1].lower() == "true"
        segs = segs[1:]
    mids = [parse_latlng(s.replace("via:", "")) for s in segs]
    locs = [parse_latlng(origin), *mids, parse_latlng(destination)]

    endpoint = "/optimized_route" if (optimize and len(mids) >= 1) else "/route"
    costing = costing_for(request)
    rad = snap_radius(request)
    lang = language_for(request)
    vi = lang.lower().startswith("vi")     # render Vietnamese narrative ourselves (Valhalla lacks vi)
    payload = {"locations": with_radius(locs, rad), "costing": costing, "units": "kilometers",
               "directions_options": {"language": lang}}
    co = costing_options(request, costing)
    if co:
        payload["costing_options"] = co
    excl = avoid_locations(request)
    if excl:
        payload["exclude_locations"] = excl
    zones = avoid_zones(request)
    if zones:
        payload["exclude_polygons"] = zones
    if (request.query_params.get("alternatives") or "").lower() in ("1", "true", "yes") and not optimize:
        payload["alternates"] = 2
    data = await valhalla(endpoint, payload)
    trip = data.get("trip")
    if (not trip or trip.get("status") != 0) and rad < 200:
        payload["locations"] = with_radius(locs, 200)   # one wider-radius retry
        data = await valhalla(endpoint, payload)
        trip = data.get("trip")
    if not trip or trip.get("status") != 0:           # last resort: escape a snapped
        payload["locations"] = with_road_snap(locs)   # service-road island (airport etc.)
        data = await valhalla(endpoint, payload)
        trip = data.get("trip")
    if not trip or trip.get("status") != 0:
        return JSONResponse({"status": "ZERO_RESULTS", "routes": [],
                             "error_message": data.get("error", "")}, status_code=200)

    def build_route(t: dict) -> dict:
        visit = t.get("locations", locs)
        legs, coords = [], []
        def snap_m(pt):
            """How far Valhalla moved a visited location from the requested coord."""
            oi = pt.get("original_index")
            if oi is None or not (0 <= oi < len(locs)):
                return None
            d = haversine_m(pt["lat"], pt.get("lon", pt.get("lng")), locs[oi]["lat"], locs[oi]["lon"])
            return round(d, 1) if d > 25 else None

        for i, leg in enumerate(t["legs"]):
            summ = leg["summary"]
            meters, secs = summ["length"] * 1000, summ["time"]
            leg_coords = decode(leg["shape"], precision=6)
            coords.extend(leg_coords if i == 0 else leg_coords[1:])   # drop the shared boundary vertex
            a, b = visit[i], visit[i + 1]
            leg_entry = {
                "distance": {"text": dist_text(meters), "value": round(meters)},
                "duration": {"text": dur_text(secs), "value": round(secs)},
                "start_location": {"lat": a["lat"], "lng": a.get("lon", a.get("lng"))},
                "end_location": {"lat": b["lat"], "lng": b.get("lon", b.get("lng"))},
                "steps": build_steps(leg, leg_coords, vi),
            }
            sm = snap_m(a)
            if sm is not None:
                leg_entry["snapped_distance_m"] = sm
            # the loop only checks each leg's START; flag the final destination too
            if i == len(t["legs"]) - 1:
                dm = snap_m(b)
                if dm is not None:
                    leg_entry["snapped_distance_destination_m"] = dm
            legs.append(leg_entry)
        r = {"summary": "", "legs": legs, "overview_polyline": {"points": encode(coords, precision=5)}}
        if optimize:
            r["waypoint_order"] = [v["original_index"] - 1 for v in visit[1:-1]
                                   if v.get("original_index", 0) not in (0, len(locs) - 1)]
        return r

    routes = [build_route(trip)]
    for alt in data.get("alternates", []):
        if alt.get("trip"):
            routes.append(build_route(alt["trip"]))
    return {"status": "OK", "routes": routes}


async def _matrix_addresses(request: Request, points: list) -> list:
    """Reverse-geocode each point for human origin/destination_addresses. Opt-in via
    ?addresses=true; cached via geocoder(); falls back to 'lat,lng' on any failure."""
    if (request.query_params.get("addresses") or "").lower() not in ("1", "true", "yes"):
        return [f'{p["lat"]},{p["lon"]}' for p in points]
    out = []
    for p in points:
        bare = f'{p["lat"]},{p["lon"]}'
        try:
            data = await geocoder("/reverse", {"lat": p["lat"], "lon": p["lon"]})
            r = data.get("result")
            out.append(_formatted(r) if r else bare)
        except HTTPException:
            out.append(bare)
    return out


@app.get("/maps/api/distancematrix/json")
async def distance_matrix(request: Request):
    require_key(request)
    origins = request.query_params.get("origins")
    destinations = request.query_params.get("destinations")
    if not origins or not destinations:
        return JSONResponse({"status": "INVALID_REQUEST", "rows": []}, status_code=400)
    sources = [parse_latlng(s) for s in origins.split("|")]
    targets = [parse_latlng(s) for s in destinations.split("|")]

    costing = costing_for(request)
    rad = snap_radius(request)
    payload = {"sources": with_radius(sources, rad), "targets": with_radius(targets, rad),
               "costing": costing, "units": "kilometers"}
    co = costing_options(request, costing)
    if co:
        payload["costing_options"] = co
    zones = avoid_zones(request)
    if zones:
        payload["exclude_polygons"] = zones
    data = await valhalla("/sources_to_targets", payload)
    matrix = data.get("sources_to_targets")
    if matrix is None:                       # whole-matrix failure (e.g. a source/target
        for snap in (with_radius, with_road_snap):   # snapped to a disconnected island) —
            payload["sources"] = snap(sources, 200) if snap is with_radius else snap(sources)
            payload["targets"] = snap(targets, 200) if snap is with_radius else snap(targets)
            matrix = (await valhalla("/sources_to_targets", payload)).get("sources_to_targets")
            if matrix is not None:           # escalate snap like Directions does
                break
    if matrix is not None and rad < 200 and any(
            c.get("distance") is None for row in matrix for c in row):
        payload["sources"] = with_radius(sources, 200)
        payload["targets"] = with_radius(targets, 200)
        m2 = (await valhalla("/sources_to_targets", payload)).get("sources_to_targets")
        if m2 is not None:
            for i, row in enumerate(matrix):
                for j, c in enumerate(row):
                    if c.get("distance") is None and m2[i][j].get("distance") is not None:
                        matrix[i][j] = m2[i][j]
    if matrix is not None and any(c.get("distance") is None for row in matrix for c in row):
        # last resort for still-unroutable cells: escape service-road islands
        payload["sources"] = with_road_snap(sources)
        payload["targets"] = with_road_snap(targets)
        m3 = (await valhalla("/sources_to_targets", payload)).get("sources_to_targets")
        if m3 is not None:
            for i, row in enumerate(matrix):
                for j, c in enumerate(row):
                    if c.get("distance") is None and m3[i][j].get("distance") is not None:
                        matrix[i][j] = m3[i][j]
    if matrix is None:
        return JSONResponse({"status": "ZERO_RESULTS", "rows": [],
                             "error_message": data.get("error", "")}, status_code=200)

    rows = []
    for row in matrix:
        elements = []
        for cell in row:
            if cell.get("distance") is None:
                elements.append({"status": "ZERO_RESULTS"})
                continue
            meters, secs = cell["distance"] * 1000, cell["time"]
            elements.append({
                "status": "OK",
                "distance": {"text": dist_text(meters), "value": round(meters)},
                "duration": {"text": dur_text(secs), "value": round(secs)},
            })
        rows.append({"elements": elements})

    return {
        "status": "OK",
        "origin_addresses": await _matrix_addresses(request, sources),
        "destination_addresses": await _matrix_addresses(request, targets),
        "rows": rows,
    }


# Valhalla maneuver type -> Google directions `maneuver` string (best-effort).
_MANEUVER = {
    9: "turn-slight-right", 10: "turn-right", 11: "turn-sharp-right",
    12: "uturn-right", 13: "uturn-left", 14: "turn-sharp-left", 15: "turn-left",
    16: "turn-slight-left", 8: "straight", 17: "straight",
    18: "ramp-right", 19: "ramp-left", 20: "ramp-right", 21: "ramp-left",
    23: "fork-right", 24: "fork-left", 25: "merge",
    26: "roundabout-right", 27: "roundabout-right",
}


def build_steps(leg: dict, leg_coords: list, vi: bool = False) -> list:
    """Map Valhalla leg maneuvers -> Google Directions steps[]. When `vi`, render the
    instruction in Vietnamese (Valhalla lacks a vi locale); else keep its English."""
    steps = []
    n = len(leg_coords)
    for mv in leg.get("maneuvers", []):
        bi = min(mv.get("begin_shape_index", 0), n - 1) if n else 0
        ei = min(mv.get("end_shape_index", bi), n - 1) if n else 0
        a = leg_coords[bi] if n else (0, 0)
        b = leg_coords[ei] if n else (0, 0)
        meters, secs = mv.get("length", 0) * 1000, mv.get("time", 0)
        step = {
            "html_instructions": vi_instruction(mv) if vi else mv.get("instruction", ""),
            "distance": {"text": dist_text(meters), "value": round(meters)},
            "duration": {"text": dur_text(secs), "value": round(secs)},
            "start_location": {"lat": a[0], "lng": a[1]},
            "end_location": {"lat": b[0], "lng": b[1]},
            "polyline": {"points": encode(leg_coords[bi:ei + 1], precision=5)},
        }
        m = _MANEUVER.get(mv.get("type"))
        if m:
            step["maneuver"] = m
        steps.append(step)
    return steps


def _bias_params(request: Request) -> dict:
    """Google-style viewport bias: ?location=lat,lng -> geocoder lat/lon."""
    loc = request.query_params.get("location") or request.query_params.get("locationbias")
    if not loc:
        return {}
    try:
        b = parse_latlng(loc.replace("circle:", "").split("@")[-1])
        return {"lat": b["lat"], "lon": b["lon"]}
    except HTTPException:
        return {}


def _feature_types(kind: str) -> list[str]:
    return {"place": ["locality", "political"], "street": ["route"],
            "poi": ["point_of_interest", "establishment"],
            "address": ["street_address"]}.get(kind, ["establishment"])


def _address_components(r: dict) -> list:
    comps = []

    def add(val, types):
        if val:
            comps.append({"long_name": val, "short_name": val, "types": types})

    add(r.get("housenumber"), ["street_number"])
    add(r.get("street"), ["route"])
    add(r.get("district"), ["administrative_area_level_2", "political"])
    add(r.get("city"), ["administrative_area_level_1", "locality", "political"])
    add(r.get("region"), ["administrative_area_level_1", "political"])
    add("Vietnam", ["country", "political"])
    return comps


def _formatted(r: dict) -> str:
    line1 = " ".join(filter(None, [r.get("housenumber"), r.get("street")]))
    seen = []
    for p in [r.get("name"), line1 or None, r.get("district"), r.get("city"), r.get("region")]:
        if p and p not in seen:
            seen.append(p)
    return ", ".join(seen) or (r.get("name") or "")


def _geo_result(r: dict) -> dict:
    return {
        "formatted_address": _formatted(r),
        "geometry": {"location": {"lat": r["lat"], "lng": r["lon"]},
                     "location_type": "APPROXIMATE"},
        "types": _feature_types(r.get("kind", "")),
        "place_id": r.get("osm_id", ""),
        "name": r.get("name"),
        "address_components": _address_components(r),
    }


@app.get("/maps/api/geocode/json")
async def geocode(request: Request):
    """Google Geocoding shape. ?address=... forward; ?latlng=lat,lng reverse."""
    require_key(request)
    latlng = request.query_params.get("latlng")
    if latlng:
        loc = parse_latlng(latlng)
        data = await geocoder("/reverse", {"lat": loc["lat"], "lon": loc["lon"]})
        r = data.get("result")
        if not r:
            return {"status": "ZERO_RESULTS", "results": []}
        results = [r]
    else:
        address = _clean_text(request.query_params.get("address"))
        if not address:
            return JSONResponse({"status": "INVALID_REQUEST", "results": []}, status_code=400)
        address = await maybe_normalize(request, address, timeout=8)
        params = {"q": address, "limit": 5}
        params.update(_bias_params(request))
        data = await geocoder("/geocode", params)
        results = data.get("results", [])
        if not results:
            return {"status": "ZERO_RESULTS", "results": []}

    return {"status": "OK", "results": [_geo_result(r) for r in results]}


@app.get("/maps/api/place/nearbysearch/json")
async def nearbysearch(request: Request):
    """Google Nearby Search. ?location=lat,lng&radius=&type=<category>&keyword=<text>."""
    require_key(request)
    loc = request.query_params.get("location")
    if not loc:
        return JSONResponse({"status": "INVALID_REQUEST", "results": []}, status_code=400)
    b = parse_latlng(loc)
    params = {"lat": b["lat"], "lon": b["lon"],
              "radius": request.query_params.get("radius", "1500")}
    if request.query_params.get("type"):
        params["category"] = request.query_params["type"]
    if request.query_params.get("keyword"):
        params["q"] = _clean_text(request.query_params["keyword"])
    data = await geocoder("/nearby", params)
    res = data.get("results", [])
    return {
        "status": "OK" if res else "ZERO_RESULTS",
        "results": [{
            "name": r["name"],
            "place_id": r.get("osm_id", ""),
            "geometry": {"location": {"lat": r["lat"], "lng": r["lon"]}},
            "types": _feature_types(r.get("kind", "")),
            "vicinity": _formatted(r),
            "category": r.get("category"),
            "distance_m": r.get("distance_m"),
        } for r in res],
    }


@app.get("/maps/api/place/details/json")
async def place_details(request: Request):
    """Google Place Details. ?place_id=<osm_id>."""
    require_key(request)
    pid = request.query_params.get("place_id")
    if not pid:
        return JSONResponse({"status": "INVALID_REQUEST", "result": {}}, status_code=400)
    data = await geocoder("/detail", {"osm_id": pid})
    r = data.get("result")
    if not r:
        return {"status": "NOT_FOUND", "result": {}}
    return {"status": "OK", "result": _geo_result(r)}


@app.get("/maps/api/place/autocomplete/json")
async def autocomplete(request: Request):
    """Google Places Autocomplete shape -> geocoder typeahead.

    Supports ?location=lat,lng viewport bias and opt-in ?normalize=1.
    """
    require_key(request)
    text = _clean_text(request.query_params.get("input"))
    if not text:
        return JSONResponse({"status": "INVALID_REQUEST", "predictions": []}, status_code=400)
    text = await maybe_normalize(request, text, timeout=2)  # typeahead must not hang on the LLM
    params = {"q": text, "limit": 8}
    params.update(_bias_params(request))
    data = await geocoder("/autocomplete", params)
    preds = data.get("results", [])
    if not preds:
        return {"status": "ZERO_RESULTS", "predictions": []}
    return {
        "status": "OK",
        "predictions": [{
            "description": ", ".join(filter(None, [p["name"], p.get("extra")])),
            "place_id": p.get("osm_id", ""),
            "structured_formatting": {
                "main_text": p["name"],
                "secondary_text": p.get("extra", ""),
            },
            "types": _feature_types(p.get("kind", "")),
        } for p in preds],
    }


# --- NullMaps-native fleet extensions (no Google equivalent) ------------------

@app.get("/v1/isochrone")
async def isochrone(request: Request):
    """Reachability polygons. ?location=lat,lng&contours=10,20 (minutes)&mode=...
    Returns Valhalla GeoJSON (FeatureCollection of contour polygons)."""
    require_key(request)
    loc = request.query_params.get("location")
    if not loc:
        return JSONResponse({"status": "INVALID_REQUEST"}, status_code=400)
    try:
        mins = [float(m) for m in (request.query_params.get("contours") or "15").split(",") if m]
    except ValueError:
        raise HTTPException(status_code=400, detail="bad contours (must be comma-separated minutes)")
    if not mins or any(m <= 0 for m in mins):
        raise HTTPException(status_code=400, detail="contours must be positive minutes")
    mins = mins[:4]   # valhalla.json max_contours
    data = await valhalla("/isochrone", {
        "locations": [parse_latlng(loc)],
        "costing": costing_for(request),
        "contours": [{"time": m} for m in mins],
        "polygons": True,
    })
    return data


@app.get("/v1/snap")
async def snap(request: Request):
    """Snap-to-roads / map-matching. ?path=lat,lng|lat,lng|...&mode=...
    Returns the matched route distance/duration + encoded polyline."""
    require_key(request)
    path = request.query_params.get("path")
    if not path:
        return JSONResponse({"status": "INVALID_REQUEST"}, status_code=400)
    shape = [parse_latlng(p) for p in path.split("|") if p]
    data = await valhalla("/trace_route", {
        "shape": shape, "costing": costing_for(request),
        "shape_match": "map_snap", "units": "kilometers",
    })
    trip = data.get("trip")
    if not trip or trip.get("status") != 0:
        return JSONResponse({"status": "ZERO_RESULTS",
                             "error_message": data.get("error", "")}, status_code=200)
    coords = []
    for leg in trip["legs"]:
        coords.extend(decode(leg["shape"], precision=6))
    s = trip["summary"]
    return {
        "status": "OK",
        "distance": {"text": dist_text(s["length"] * 1000), "value": round(s["length"] * 1000)},
        "duration": {"text": dur_text(s["time"]), "value": round(s["time"])},
        "snapped_polyline": {"points": encode(coords, precision=5)},
    }
