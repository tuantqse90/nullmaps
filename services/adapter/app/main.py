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
PHOTON_URL = os.environ.get("PHOTON_URL", "http://photon:2322").rstrip("/")

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


@app.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "service": "nullmaps-adapter",
        "phase": 4,
        "live": ["directions", "distancematrix"],
        "pending": ["geocode", "place/autocomplete"],
    }


@app.get("/maps/api/directions/json")
async def directions(request: Request):
    require_key(request)
    origin = request.query_params.get("origin")
    destination = request.query_params.get("destination")
    if not origin or not destination:
        return JSONResponse({"status": "INVALID_REQUEST", "routes": []}, status_code=400)
    locs = [parse_latlng(origin)]
    # Google waypoints: "via:lat,lng|lat,lng" or "lat,lng|lat,lng"
    for wp in filter(None, (request.query_params.get("waypoints") or "").split("|")):
        locs.append(parse_latlng(wp.replace("via:", "")))
    locs.append(parse_latlng(destination))

    data = await valhalla("/route", {
        "locations": locs,
        "costing": costing_for(request),
        "units": "kilometers",
    })
    trip = data.get("trip")
    if not trip or trip.get("status") != 0:
        return JSONResponse({"status": "ZERO_RESULTS", "routes": [],
                             "error_message": data.get("error", "")}, status_code=200)

    legs, coords = [], []
    for i, leg in enumerate(trip["legs"]):
        summ = leg["summary"]
        meters, secs = summ["length"] * 1000, summ["time"]
        a, b = locs[i], locs[i + 1]
        legs.append({
            "distance": {"text": dist_text(meters), "value": round(meters)},
            "duration": {"text": dur_text(secs), "value": round(secs)},
            "start_location": {"lat": a["lat"], "lng": a["lon"]},
            "end_location": {"lat": b["lat"], "lng": b["lon"]},
            "steps": [],
        })
        coords.extend(decode(leg["shape"], precision=6))

    return {
        "status": "OK",
        "routes": [{
            "summary": "",
            "legs": legs,
            "overview_polyline": {"points": encode(coords, precision=5)},
        }],
    }


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


def _pending(name: str):
    return JSONResponse(
        {"status": "UNAVAILABLE",
         "error_message": f"{name} requires Phase 3 (Photon geocoder), not yet deployed. "
                          f"Routing/tiles are live; see CLAUDE.md roadmap."},
        status_code=503,
    )


@app.get("/maps/api/geocode/json")
async def geocode(request: Request):
    require_key(request)
    return _pending("Geocoding")


@app.get("/maps/api/place/autocomplete/json")
async def autocomplete(request: Request):
    require_key(request)
    return _pending("Places Autocomplete")
