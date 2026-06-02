"""NullMaps Google/Goong-compat adapter (Phase 4).

Maps Google Maps API request/response shapes onto the native NullMaps engines so
existing apps can repoint without rewriting client code. Goong's REST shapes mirror
Google's closely, so this Google-compatible surface covers most of Goong too.

Live now (Valhalla is up):
  GET /maps/api/directions/json        -> Valhalla /route
  GET /maps/api/distancematrix/json    -> Valhalla /sources_to_targets

Pending Phase 3 (Photon) — return a clear 503 until geocoding is online:
  GET /maps/api/geocode/json
  GET /maps/api/place/autocomplete/json

Auth: single shared API_KEY, checked on every endpoint except /healthz.
Pass it Google-style as ?key=... or as an X-API-Key header.
"""
from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .polyline import reencode, encode, decode

app = FastAPI(title="NullMaps Adapter", version="0.4.0")

API_KEY = os.environ.get("API_KEY", "")
VALHALLA_URL = os.environ.get("VALHALLA_URL", "http://valhalla:8002").rstrip("/")
GEOCODER_URL = os.environ.get("GEOCODER_URL", "http://geocoder:2322").rstrip("/")
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
}


def require_key(request: Request) -> None:
    """One shared key. Accept ?key= (Google style) or X-API-Key header."""
    supplied = request.query_params.get("key") or request.headers.get("x-api-key")
    if not API_KEY or supplied != API_KEY:
        raise HTTPException(status_code=403, detail="invalid API key")


def parse_latlng(s: str) -> dict:
    """'10.77,106.70' -> {'lat':10.77,'lon':106.70}. Raises on bad input."""
    try:
        lat, lon = (float(x) for x in s.split(",", 1))
        return {"lat": lat, "lon": lon}
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail=f"bad lat,lng: {s!r}")


def costing_for(request: Request) -> str:
    mode = request.query_params.get("mode") or request.query_params.get("vehicle")
    return COSTING.get((mode or "").lower(), "motor_scooter")


def dist_text(meters: float) -> str:
    return f"{meters/1000:.1f} km" if meters >= 1000 else f"{round(meters)} m"


def dur_text(seconds: float) -> str:
    m = round(seconds / 60)
    if m < 60:
        return f"{m} min"
    return f"{m//60} h {m%60} min"


async def valhalla(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.post(f"{VALHALLA_URL}{path}", json=payload)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"routing engine unreachable: {e}")
    return r.json() if r.content else {}


async def geocoder(path: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(f"{GEOCODER_URL}{path}", params=params)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"geocoder unreachable: {e}")
    return r.json() if r.content else {}


async def maybe_normalize(request: Request, text: str) -> str:
    """Optional Phase-5 AI cleanup, opt-in via ?normalize=1. Fail-open: any
    error or unconfigured normalizer returns the input unchanged."""
    flag = (request.query_params.get("normalize") or "").lower()
    if not NORMALIZER_URL or flag not in ("1", "true", "yes"):
        return text
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(f"{NORMALIZER_URL}/normalize", params={"q": text})
        return (r.json().get("normalized") or text) if r.content else text
    except httpx.HTTPError:
        return text


@app.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "service": "nullmaps-adapter",
        "phase": 4,
        "live": ["directions", "distancematrix", "geocode", "place/autocomplete"],
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
    data = await valhalla(endpoint, {
        "locations": locs, "costing": costing_for(request), "units": "kilometers",
    })
    trip = data.get("trip")
    if not trip or trip.get("status") != 0:
        return JSONResponse({"status": "ZERO_RESULTS", "routes": [],
                             "error_message": data.get("error", "")}, status_code=200)

    # Visiting order (optimized_route reorders; each location carries original_index)
    visit = trip.get("locations", locs)

    def pt(i):
        v = visit[i]
        return {"lat": v["lat"], "lng": v.get("lon", v.get("lng"))}

    legs, coords = [], []
    for i, leg in enumerate(trip["legs"]):
        summ = leg["summary"]
        meters, secs = summ["length"] * 1000, summ["time"]
        legs.append({
            "distance": {"text": dist_text(meters), "value": round(meters)},
            "duration": {"text": dur_text(secs), "value": round(secs)},
            "start_location": pt(i),
            "end_location": pt(i + 1),
            "steps": [],
        })
        coords.extend(decode(leg["shape"], precision=6))

    route = {"summary": "", "legs": legs,
             "overview_polyline": {"points": encode(coords, precision=5)}}
    if optimize:
        # order of the intermediate waypoints by their original index (0-based)
        order = [v["original_index"] - 1 for v in visit[1:-1]
                 if v.get("original_index", 0) not in (0, len(locs) - 1)]
        route["waypoint_order"] = order
    return {"status": "OK", "routes": [route]}


@app.get("/maps/api/distancematrix/json")
async def distance_matrix(request: Request):
    require_key(request)
    origins = request.query_params.get("origins")
    destinations = request.query_params.get("destinations")
    if not origins or not destinations:
        return JSONResponse({"status": "INVALID_REQUEST", "rows": []}, status_code=400)
    sources = [parse_latlng(s) for s in origins.split("|")]
    targets = [parse_latlng(s) for s in destinations.split("|")]

    data = await valhalla("/sources_to_targets", {
        "sources": sources,
        "targets": targets,
        "costing": costing_for(request),
        "units": "kilometers",
    })
    matrix = data.get("sources_to_targets")
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
        "origin_addresses": [f'{s["lat"]},{s["lon"]}' for s in sources],
        "destination_addresses": [f'{t["lat"]},{t["lon"]}' for t in targets],
        "rows": rows,
    }


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
        address = request.query_params.get("address")
        if not address:
            return JSONResponse({"status": "INVALID_REQUEST", "results": []}, status_code=400)
        address = await maybe_normalize(request, address)
        params = {"q": address, "limit": 5}
        params.update(_bias_params(request))
        data = await geocoder("/geocode", params)
        results = data.get("results", [])
        if not results:
            return {"status": "ZERO_RESULTS", "results": []}

    return {
        "status": "OK",
        "results": [{
            "formatted_address": ", ".join(filter(None, [r["name"], r.get("extra")])),
            "geometry": {"location": {"lat": r["lat"], "lng": r["lon"]},
                         "location_type": "APPROXIMATE"},
            "types": _feature_types(r.get("kind", "")),
            "place_id": r.get("osm_id", ""),
        } for r in results],
    }


@app.get("/maps/api/place/autocomplete/json")
async def autocomplete(request: Request):
    """Google Places Autocomplete shape -> geocoder typeahead.

    Supports ?location=lat,lng viewport bias and opt-in ?normalize=1.
    """
    require_key(request)
    text = request.query_params.get("input")
    if not text:
        return JSONResponse({"status": "INVALID_REQUEST", "predictions": []}, status_code=400)
    text = await maybe_normalize(request, text)
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
    mins = [float(m) for m in (request.query_params.get("contours") or "15").split(",") if m]
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
