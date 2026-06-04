#!/usr/bin/env python3
"""Backend behavioural + accuracy probe for NullMaps.

Unlike be_fuzz.py (which only asserts "never crashes"), this asserts CORRECTNESS
against a curated set of invariants that real usage depends on:

  - ROUTES: city-pairs and "hard" destinations (airports, parks, bus/rail
    stations, cross-province) MUST route (status OK). These are the cases that
    regressed historically (service-road islands, polygon centroids).
  - GEOCODE: known queries MUST resolve within a tolerance of the expected point
    — this catches the "wrong province" class (e.g. Nguyễn Duy Trinh -> Bình Phước)
    and legacy-district ranking regressions.
  - MATRIX / ISOCHRONE: basic shape + non-empty results.

Each invariant that fails is reported. Add new rows as you discover edge cases —
this file is the regression memory for "the map gave a dumb answer".

Usage:
  NM_BASE=https://maps.nullshift.sh API_KEY=... python3 harness/be_probe.py
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---- invariants --------------------------------------------------------------

# Geocode: (query, expected_lat, expected_lon, tolerance_km). Tolerances are loose
# — we only care that it lands in the right city/area, not the exact rooftop.
# (query, expected_lat, expected_lon, tol_km, bias "lat,lon" | None = default HCMC).
# Bias mirrors the app's viewport — a Hà Nội query is run while viewing Hà Nội.
GEOCODE = [
    ("543 Nguyễn Duy Trinh P Bình Trưng Đông", 10.79, 106.78, 6, None),   # was Bình Phước (~90km)
    ("Nguyễn Duy Trinh", 10.79, 106.78, 8, None),                         # bare street, biased
    ("bình thạnh", 10.81, 106.71, 6, None),                               # legacy district, not central VN
    ("q1", 10.776, 106.701, 5, None),
    ("gò vấp", 10.838, 106.665, 6, None),
    ("hoàn kiếm", 21.03, 105.85, 7, "21.03,105.85"),                      # Hà Nội (biased to the region)
    ("nguyen hue", 10.775, 106.70, 6, None),                              # diacritic-insensitive input
    ("Bến Thành", 10.77, 106.70, 6, None),
    ("sân bay Tân Sơn Nhất", 10.818, 106.657, 6, None),
]

# Routes that MUST return OK. origin/destination are place names (geocoded) or
# "lat,lng". The hard cases are big POIs / cross-province.
ROUTES = [
    ("Bến Thành", "sân bay Tân Sơn Nhất", "driving"),
    ("sân bay Tân Sơn Nhất", "Bến Thành", "two_wheeler"),
    ("543 Nguyễn Duy Trinh P Bình Trưng Đông", "sân bay Tân Sơn Nhất", "driving"),
    ("Công viên Tao Đàn", "Bến Thành", "driving"),
    ("Chợ Bến Thành", "Bến xe Miền Đông", "two_wheeler"),
    ("10.7769,106.7009", "10.0452,105.7469", "driving"),             # HCMC -> Cần Thơ (cross-province)
]

# Bias for geocoding (HCMC center) — most queries above are southern.
BIAS = "10.776,106.70"

# Cloudflare (front of the prod gateway) 403s the default "Python-urllib" UA.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ---- http --------------------------------------------------------------------


def _get(base, key, path, params, timeout=30):
    q = dict(params); q["key"] = key
    url = base + path + "?" + urllib.parse.urlencode(q)
    last = (None, {})
    for attempt in range(3):                     # absorb transient Cloudflare 403/timeout bursts
        req = urllib.request.Request(url, headers={"X-API-Key": key, "User-Agent": UA})
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
            return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 403:                    # Cloudflare challenge, not the adapter
                last = (403, {}); time.sleep(1 + attempt); continue
            try:
                return e.code, json.loads(e.read())
            except Exception:
                return e.code, {}
        except Exception as e:
            last = (None, {"_err": str(e)}); time.sleep(1 + attempt); continue
    return last


def haversine_km(a, b, c, d):
    R = 6371.0
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = math.radians(c - a), math.radians(d - b)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def geocode(base, key, q, bias=None):
    _, d = _get(base, key, "/maps/api/geocode/json", {"address": q, "location": bias or BIAS})
    r = (d.get("results") or [None])[0]
    if not r:
        return None
    loc = r["geometry"]["location"]
    return loc["lat"], loc["lng"], r.get("formatted_address") or r.get("name")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("NM_BASE", "http://localhost:8088"))
    ap.add_argument("--key", default=os.environ.get("API_KEY", "secret"))
    ap.add_argument("--pace", type=float, default=float(os.environ.get("NM_PACE", "0.1")))
    args = ap.parse_args()
    base, key = args.base, args.key
    print(f"# be_probe -> {base}")
    fails = []

    print("## geocode accuracy")
    for q, elat, elon, tol, bias in GEOCODE:
        g = geocode(base, key, q, bias)
        time.sleep(args.pace)
        if not g:
            fails.append(("geocode", q, "ZERO_RESULTS"))
            print(f"  ✗ {q!r}: no result"); continue
        dist = haversine_km(elat, elon, g[0], g[1])
        ok = dist <= tol
        print(f"  {'✓' if ok else '✗'} {q!r}: {g[2]!r} ({g[0]:.4f},{g[1]:.4f})  {dist:.1f}km from expected (tol {tol})")
        if not ok:
            fails.append(("geocode", q, f"{dist:.1f}km off (got {g[0]:.4f},{g[1]:.4f}, want {elat},{elon})"))

    print("## routes must resolve")
    for o, dst, mode in ROUTES:
        oc = o if "," in o and o.replace(",", "").replace(".", "").replace("-", "").isdigit() else None
        dc = dst if "," in dst and dst.replace(",", "").replace(".", "").replace("-", "").isdigit() else None
        if oc is None:
            g = geocode(base, key, o); time.sleep(args.pace)
            oc = f"{g[0]},{g[1]}" if g else None
        if dc is None:
            g = geocode(base, key, dst); time.sleep(args.pace)
            dc = f"{g[0]},{g[1]}" if g else None
        if not oc or not dc:
            fails.append(("route", f"{o} -> {dst}", "geocode failed")); print(f"  ✗ {o} -> {dst}: geocode failed"); continue
        _, d = _get(base, key, "/maps/api/directions/json", {"origin": oc, "destination": dc, "mode": mode})
        time.sleep(args.pace)
        st = d.get("status")
        km = round(sum(l["distance"]["value"] for l in d["routes"][0]["legs"]) / 1000, 1) if st == "OK" else None
        print(f"  {'✓' if st == 'OK' else '✗'} {o} -> {dst} ({mode}): {st} {km or d.get('error_message','')}")
        if st != "OK":
            fails.append(("route", f"{o} -> {dst}", st))

    print("## matrix + isochrone")
    _, d = _get(base, key, "/maps/api/distancematrix/json", {"origins": "10.7769,106.7009", "destinations": "10.0452,105.7469", "mode": "driving"})
    time.sleep(args.pace)
    cell = (d.get("rows") or [{}])[0].get("elements", [{}])[0].get("status")
    print(f"  {'✓' if cell == 'OK' else '✗'} matrix HCMC->CanTho: {cell}")
    if cell != "OK":
        fails.append(("matrix", "HCMC->CanTho", cell))
    _, d = _get(base, key, "/v1/isochrone", {"location": "10.7769,106.7009", "contours": "5,10,15", "mode": "two_wheeler"})
    n = len(d.get("features", []))
    print(f"  {'✓' if n >= 1 else '✗'} isochrone: {n} polygons")
    if n < 1:
        fails.append(("isochrone", "5,10,15", f"{n} features"))

    print(f"\n# {len(fails)} invariant failures")
    if fails:
        print(json.dumps([{"kind": k, "case": c, "detail": d} for k, c, d in fails], ensure_ascii=False))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
