"""NullMaps Google/Goong-compat adapter (Phase 4).

Today this is a scaffold: only /healthz is live. Real Google-shaped endpoints are
added in Phase 4, ONLY for the endpoints the operator's apps actually call.

Auth: single shared API_KEY from env, checked on every non-health request.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request

app = FastAPI(title="NullMaps Adapter", version="0.1.0")

API_KEY = os.environ.get("API_KEY", "")


def require_key(request: Request) -> None:
    """One shared key. Accept ?key= (Google style) or X-API-Key header."""
    supplied = request.query_params.get("key") or request.headers.get("x-api-key")
    if not API_KEY or supplied != API_KEY:
        raise HTTPException(status_code=403, detail="invalid API key")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": "nullmaps-adapter", "phase": 4, "endpoints": []}


# --- Phase 4 endpoints go here, e.g. ------------------------------------------
# @app.get("/maps/api/directions/json")   # Google Directions shape -> Valhalla /route
# @app.get("/maps/api/distancematrix/json")
# @app.get("/maps/api/geocode/json")
# @app.get("/maps/api/place/autocomplete/json")
# Each: require_key(request), translate params, call the native engine, reshape to Google JSON.
