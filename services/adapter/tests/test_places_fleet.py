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
