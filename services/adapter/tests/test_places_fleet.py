"""Adapter hardening: bounded rate-limit/metrics dicts, Google-shaped errors,
and the places/fleet endpoints (engines mocked)."""
import importlib
import os

from fastapi.testclient import TestClient


def load(api_key="secret", rate_per_min=None):
    os.environ["API_KEY"] = api_key
    if rate_per_min is not None:
        os.environ["RATE_LIMIT_PER_MIN"] = str(rate_per_min)
    else:
        os.environ.pop("RATE_LIMIT_PER_MIN", None)
    import app.main as m
    importlib.reload(m)
    return m


def test_rate_limit_dicts_are_bounded():
    m = load()
    c = TestClient(m.app)
    for i in range(1100):  # > maxsize (1024) distinct keys
        c.get("/maps/api/geocode/json", params={"address": "x", "key": f"k{i}"})
    assert len(m._by_key) <= 1024
    assert len(m._rl) <= 1024


def test_missing_key_returns_google_request_denied():
    m = load()
    c = TestClient(m.app)
    r = c.get("/maps/api/geocode/json", params={"address": "x"})  # no key
    assert r.status_code == 403
    b = r.json()
    assert b["status"] == "REQUEST_DENIED"
    assert "error_message" in b


def test_bad_latlng_returns_google_invalid_request():
    m = load()
    c = TestClient(m.app)
    r = c.get("/maps/api/geocode/json", params={"latlng": "not-a-coord", "key": "secret"})
    assert r.status_code == 400
    assert r.json()["status"] == "INVALID_REQUEST"


def test_metrics_keeps_default_error_shape():
    m = load()
    c = TestClient(m.app)
    r = c.get("/metrics")  # not under /maps or /v1 -> default {detail}
    assert r.status_code == 403
    assert "detail" in r.json()
