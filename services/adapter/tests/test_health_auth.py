"""Phase-4 adapter scaffold tests: health is open, key-gated deps reject bad keys.

Run:  cd services/adapter && pip install -r requirements.txt pytest && pytest
"""
import importlib
import os

from fastapi import Request
from fastapi.testclient import TestClient
import pytest


def load_app(api_key: str):
    os.environ["API_KEY"] = api_key
    import app.main as m
    importlib.reload(m)
    return m


def test_healthz_open_and_shape():
    m = load_app("secret")
    client = TestClient(m.app)
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "nullmaps-adapter"
    assert body["phase"] == 4
    assert "directions" in body["live"]
    assert "geocode" in body["live"]      # live since Phase 3
    assert body["pending"] == []


def _fake_request(query="", header=None):
    headers = [(b"x-api-key", header.encode())] if header else []
    scope = {
        "type": "http",
        "query_string": query.encode(),
        "headers": headers,
    }
    return Request(scope)


def test_require_key_accepts_query_key():
    m = load_app("secret")
    # should not raise
    m.require_key(_fake_request(query="key=secret"))


def test_require_key_accepts_header():
    m = load_app("secret")
    m.require_key(_fake_request(header="secret"))


def test_require_key_rejects_wrong_key():
    m = load_app("secret")
    with pytest.raises(Exception):
        m.require_key(_fake_request(query="key=nope"))


def test_require_key_rejects_when_unset():
    m = load_app("")  # no server key configured -> always reject
    with pytest.raises(Exception):
        m.require_key(_fake_request(query="key=anything"))
