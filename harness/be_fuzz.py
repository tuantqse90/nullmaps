#!/usr/bin/env python3
"""Backend robustness fuzzer for the NullMaps adapter.

Throws malformed / boundary inputs at every Google-compat + native endpoint and
flags anything that is NOT graceful:
  - any 5xx (an uncaught exception leaking a stack trace)
  - a 4xx/2xx whose body is not valid JSON, or lacks a Google-shaped "status"
  - a request that hangs past the timeout

It does NOT assert specific results — it asserts the service never crashes and
always answers in the documented shape. Pair it with be_probe.py (correctness).

Usage:
  NM_BASE=https://maps.nullshift.sh API_KEY=... python3 harness/be_fuzz.py
  python3 harness/be_fuzz.py --base http://localhost:8088 --key secret --pace 0.05

Auth: hits the key-gated /maps/* and /v1/* surfaces directly with X-API-Key
(NOT the basic-auth /app proxy — that's the front-end's path, see fe_smoke.mjs).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---- fuzz vocabularies -------------------------------------------------------

BAD_LATLNG = [
    "nan,106.7", "inf,106.7", "-inf,106.7", "1e500,106.7", "10.7,nan",
    "200,106.7", "10.7,400", "-91,0", "0,181",            # out of range
    "10.7", "10.7,106.7,99", "abc,def", "", ",", "10.7,",
    "10.7;106.7", "10.7 106.7", "<script>,1", "10.7,106.7'",
]
BAD_TEXT = [
    "", " ", "a", "'; DROP TABLE features;--", "%00", "\x00abc",
    "ăâđêôơư " * 50, "🚗🛵" * 20, "x" * 8000, "../../etc/passwd",
    "%' OR '1'='1", "{{7*7}}", "\n\r\t",
]
BAD_INT = ["", "abc", "-1", "0", "1e9", "999999999999", "nan", "1.5", "-0"]

# ---- endpoint fuzz matrix ----------------------------------------------------
# Each case: (path, base_params, field_to_fuzz, vocabulary). base_params provide
# a minimally-valid request; the fuzzer overrides one field at a time.

VALID_LL = "10.7769,106.7009"

# Cloudflare (in front of the prod gateway) 403s the default "Python-urllib" UA, so
# send a browser-like one. Against a localhost/internal adapter this is harmless.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def cases():
    out = []
    # directions
    for f, vocab in [("origin", BAD_LATLNG), ("destination", BAD_LATLNG)]:
        out.append(("/maps/api/directions/json", {"origin": VALID_LL, "destination": "10.79,106.72"}, f, vocab))
    out += [
        ("/maps/api/directions/json", {"origin": VALID_LL, "destination": "10.79,106.72"}, "mode",
         ["", "teleport", "DRIVING", "two_wheeler;", "rocket"]),
        ("/maps/api/directions/json", {"origin": VALID_LL, "destination": "10.79,106.72"}, "waypoints",
         ["optimize:true|", "via:", "|||", "optimize:maybe|10.7,106.7",
          "|".join(["10.7,106.7"] * 600), "10.7,106.7|nan,1"]),
        ("/maps/api/directions/json", {"origin": VALID_LL, "destination": "10.79,106.72"}, "avoid_zones",
         ["{}", "[1,2,3]", '{"type":"Polygon"}', '{"type":"Polygon","coordinates":[[[1,2]]]}',
          "not json", "{" * 5000, '{"type":"Point","coordinates":[1,2]}']),
        ("/maps/api/directions/json", {"origin": VALID_LL, "destination": "10.79,106.72"}, "top_speed",
         BAD_INT),
    ]
    # matrix
    out += [
        ("/maps/api/distancematrix/json", {"origins": VALID_LL, "destinations": "10.79,106.72"}, "origins",
         BAD_LATLNG + ["|".join(["10.7,106.7"] * 200)]),
        ("/maps/api/distancematrix/json", {"origins": VALID_LL, "destinations": "10.79,106.72"}, "destinations",
         BAD_LATLNG),
    ]
    # geocode / reverse
    out += [
        ("/maps/api/geocode/json", {}, "address", BAD_TEXT),
        ("/maps/api/geocode/json", {}, "latlng", BAD_LATLNG),
        ("/maps/api/geocode/json", {"address": "q1"}, "location", BAD_LATLNG),
    ]
    # places
    out += [
        ("/maps/api/place/autocomplete/json", {}, "input", BAD_TEXT),
        ("/maps/api/place/autocomplete/json", {"input": "ben thanh"}, "location", BAD_LATLNG),
        ("/maps/api/place/nearbysearch/json", {"location": VALID_LL}, "radius", BAD_INT),
        ("/maps/api/place/nearbysearch/json", {"location": VALID_LL}, "type", BAD_TEXT),
        ("/maps/api/place/nearbysearch/json", {}, "location", BAD_LATLNG),
        ("/maps/api/place/details/json", {}, "place_id", BAD_TEXT),
    ]
    # native fleet
    out += [
        ("/v1/isochrone", {"location": VALID_LL}, "contours", ["", "abc", "0", "-5", "5;10", "1," * 50, "999999"]),
        ("/v1/isochrone", {}, "location", BAD_LATLNG),
        ("/v1/snap", {}, "path", BAD_LATLNG + ["|".join(["10.7,106.7"] * 500)]),
    ]
    return out


# ---- runner ------------------------------------------------------------------

def fetch(base, key, path, params, timeout):
    q = dict(params)
    q["key"] = key
    url = base + path + "?" + urllib.parse.urlencode(q)
    for attempt in range(3):                     # absorb transient Cloudflare 403/timeout bursts
        req = urllib.request.Request(url, headers={"X-API-Key": key, "User-Agent": UA})
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
            return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code == 403:                    # Cloudflare bot challenge, not the adapter
                time.sleep(1 + attempt); continue
            return e.code, e.read()
        except Exception as e:                   # timeout / connection
            if attempt == 2:
                return None, str(e).encode()
            time.sleep(1 + attempt)
    return None, b"(retries exhausted)"


def check(status, body):
    """Return an anomaly string, or None if the response is graceful."""
    if status is None:
        return f"NO RESPONSE ({body.decode(errors='replace')[:80]})"
    if status >= 500:
        return f"HTTP {status} (5xx) body={body.decode(errors='replace')[:120]!r}"
    # 2xx/4xx must be Google-shaped JSON with a status field (snap/isochrone may
    # return raw Valhalla GeoJSON on success -> allow a FeatureCollection too).
    try:
        d = json.loads(body)
    except Exception:
        return f"HTTP {status} non-JSON body={body.decode(errors='replace')[:120]!r}"
    if isinstance(d, dict) and ("status" in d or d.get("type") == "FeatureCollection"):
        return None
    if isinstance(d, dict) and "trip" in d:      # isochrone passthrough edge
        return None
    return f"HTTP {status} JSON without 'status' keys={list(d)[:6] if isinstance(d, dict) else type(d).__name__}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("NM_BASE", "http://localhost:8088"))
    ap.add_argument("--key", default=os.environ.get("API_KEY", "secret"))
    ap.add_argument("--pace", type=float, default=float(os.environ.get("NM_PACE", "0.05")))
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    print(f"# be_fuzz -> {args.base}  (pace {args.pace}s)")
    total, anomalies = 0, []
    for path, base_params, field, vocab in cases():
        for val in vocab:
            params = dict(base_params); params[field] = val
            status, body = fetch(args.base, args.key, path, params, args.timeout)
            total += 1
            a = check(status, body)
            if a:
                anomalies.append((path, field, val[:60], a))
                print(f"  ✗ {path}  {field}={val[:40]!r}\n      {a}")
            time.sleep(args.pace)

    print(f"\n# {total} fuzz requests, {len(anomalies)} anomalies")
    if anomalies:
        print(json.dumps([{"path": p, "field": f, "value": v, "anomaly": a} for p, f, v, a in anomalies], ensure_ascii=False))
    sys.exit(1 if anomalies else 0)


if __name__ == "__main__":
    main()
