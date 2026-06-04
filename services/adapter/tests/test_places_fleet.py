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


# --- canned engine responses ---------------------------------------------------
async def fake_reverse(path, params):
    return {"result": {"osm_id": "n9", "name": "Chợ Bến Thành", "kind": "poi",
                       "lat": 10.7725, "lon": 106.6980, "district": "Quận 1", "city": "HCMC"}}


async def fake_results(path, params):
    return {"results": [{"osm_id": "n1", "name": "Bến Thành", "kind": "poi",
                        "lat": 10.7704, "lon": 106.6951, "extra": "HCMC",
                        "category": "marketplace", "distance_m": 120}]}


async def fake_detail(path, params):
    return {"result": {"osm_id": "n1", "name": "Bến Thành", "kind": "poi",
                       "lat": 10.7704, "lon": 106.6951}}


async def fake_isochrone(path, payload):
    return {"type": "FeatureCollection", "features": [{"type": "Feature"}]}


async def fake_trace(path, payload):
    from app.polyline import encode
    shape6 = encode([(10.77, 106.69), (10.79, 106.72)], precision=6)
    return {"trip": {"status": 0, "summary": {"length": 3.1, "time": 240},
                    "legs": [{"shape": shape6}]}}


def test_reverse_geocode_shape(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "geocoder", fake_reverse)
    c = TestClient(m.app)
    r = c.get("/maps/api/geocode/json", params={"latlng": "10.7725,106.6980", "key": "secret"})
    assert r.status_code == 200
    g = r.json()["results"][0]
    assert g["geometry"]["location"] == {"lat": 10.7725, "lng": 106.6980}


def test_autocomplete_shape(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "geocoder", fake_results)
    c = TestClient(m.app)
    r = c.get("/maps/api/place/autocomplete/json", params={"input": "ben thanh", "key": "secret"})
    b = r.json()
    assert b["status"] == "OK"
    assert b["predictions"][0]["structured_formatting"]["main_text"] == "Bến Thành"


def test_nearbysearch_shape(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "geocoder", fake_results)
    c = TestClient(m.app)
    r = c.get("/maps/api/place/nearbysearch/json",
              params={"location": "10.77,106.69", "radius": "500", "key": "secret"})
    b = r.json()
    assert b["status"] == "OK"
    assert b["results"][0]["distance_m"] == 120


def test_place_details_shape(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "geocoder", fake_detail)
    c = TestClient(m.app)
    r = c.get("/maps/api/place/details/json", params={"place_id": "n1", "key": "secret"})
    assert r.json()["result"]["name"] == "Bến Thành"


def test_isochrone_passthrough(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "valhalla", fake_isochrone)
    c = TestClient(m.app)
    r = c.get("/v1/isochrone", params={"location": "10.77,106.69", "contours": "10", "key": "secret"})
    assert r.json()["type"] == "FeatureCollection"


def test_snap_shape(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "valhalla", fake_trace)
    c = TestClient(m.app)
    r = c.get("/v1/snap", params={"path": "10.77,106.69|10.79,106.72", "key": "secret"})
    b = r.json()
    assert b["status"] == "OK"
    assert b["distance"]["value"] == 3100
    assert b["snapped_polyline"]["points"]


def test_rate_limit_429(monkeypatch):
    m = load(rate_per_min=1)
    monkeypatch.setattr(m, "geocoder", fake_results)
    c = TestClient(m.app)
    first = c.get("/maps/api/geocode/json", params={"address": "a", "key": "secret"})
    second = c.get("/maps/api/geocode/json", params={"address": "b", "key": "secret"})
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["status"] == "OVER_QUERY_LIMIT"


def test_photon_feature_maps_to_internal_shape():
    m = load()
    f = {"geometry": {"coordinates": [106.7003, 10.7773]},
         "properties": {"name": "Highlands Coffee", "osm_id": 42, "osm_type": "N",
                        "osm_key": "amenity", "osm_value": "cafe",
                        "district": "Quận 1", "city": "Hồ Chí Minh", "state": "Hồ Chí Minh"}}
    r = m._photon_feature(f)
    assert r["name"] == "Highlands Coffee"
    assert (r["lat"], r["lon"]) == (10.7773, 106.7003)
    assert r["kind"] == "poi" and r["category"] == "cafe"
    assert r["extra"] == "Quận 1, Hồ Chí Minh"          # context, deduped (state == city)
    assert r["osm_id"] == "N42"


def test_photon_feature_kinds_and_address_name():
    m = load()
    street = m._photon_feature({"geometry": {"coordinates": [1, 2]},
                                "properties": {"name": "Nguyễn Huệ", "osm_key": "highway", "osm_value": "primary"}})
    assert street["kind"] == "street"
    addr = m._photon_feature({"geometry": {"coordinates": [1, 2]},
                              "properties": {"housenumber": "543", "street": "Nguyễn Duy Trinh"}})
    assert addr["kind"] == "address" and addr["name"] == "543 Nguyễn Duy Trinh"
    place = m._photon_feature({"geometry": {"coordinates": [1, 2]},
                               "properties": {"name": "Bến Thành", "type": "suburb"}})
    assert place["kind"] == "place"


def test_split_housenumber():
    m = load()
    assert m._split_housenumber("543 Nguyễn Duy Trinh") == ("543", "Nguyễn Duy Trinh")
    assert m._split_housenumber("12A Lê Lợi") == ("12A", "Lê Lợi")
    assert m._split_housenumber("250/5 Cách Mạng Tháng Tám") == ("250/5", "Cách Mạng Tháng Tám")
    assert m._split_housenumber("Nguyễn Huệ") == (None, "Nguyễn Huệ")     # no leading number
    assert m._split_housenumber("123") == (None, "123")                  # number only, no street
    assert m._split_housenumber("highlands") == (None, "highlands")


def test_is_district_q():
    m = load()
    assert m._is_district_q("q7") and m._is_district_q("Q1") and m._is_district_q("quận 12")
    assert m._is_district_q("quan 3") and m._is_district_q("q.5")
    assert not m._is_district_q("q7 boulevard")   # extra text -> a POI, not the district
    assert not m._is_district_q("q13")            # no district 13
    assert not m._is_district_q("nguyễn huệ")
    assert not m._is_district_q(None)


# --- Overture business-POI FTS index ---------------------------------------

def _build_overture_db(path):
    """Build a tiny Overture FTS DB matching the production schema/fold."""
    import sqlite3, unicodedata
    def fold(s):
        s = (s or "").replace("Đ", "D").replace("đ", "d")
        return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower().strip()
    rows = [  # name, lat, lon, category, context, conf, ward, province, phone, website, brand
        ("Highlands Coffee", 10.780, 106.700, "cafe", "90 CMT8", 80, "Phường Bến Thành", "Thành phố Hồ Chí Minh", None, None, "Highlands Coffee"),
        ("Highlands Coffee", 21.030, 105.850, "cafe", "Hà Nội", 90, "Phường Hoàn Kiếm", "Thành phố Hà Nội", None, None, "Highlands Coffee"),  # far, higher conf
        ("Kaldivie Coffee", 10.781, 106.701, "cafe", "76A Đường Lê Lai", 70, "Phường Bến Thành", "Thành phố Hồ Chí Minh", "+84281234567", "https://kaldivie.vn", None),
        # freeform tail carries STALE pre-2025 admin ("Tỉnh Kiên Giang") -> must be stripped
        ("Phở Hòa Pasteur", 10.790, 106.680, "restaurant", "260C Pasteur, P. cũ, Tỉnh Kiên Giang", 60, "Phường Xuân Hòa", "Thành phố Hồ Chí Minh", None, None, None),
    ]
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE places(name TEXT, lat REAL, lon REAL, category TEXT, context TEXT, conf INTEGER, "
                "folded TEXT, ward TEXT, province TEXT, phone TEXT, website TEXT, social TEXT, brand TEXT, cats TEXT)")
    con.executemany("INSERT INTO places(name,lat,lon,category,context,conf,folded,ward,province,phone,website,brand) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(n, la, lo, cat, ctx, cf, fold(n), w, pr, ph, web, br) for (n, la, lo, cat, ctx, cf, w, pr, ph, web, br) in rows])
    con.execute("CREATE VIRTUAL TABLE places_fts USING fts5(folded, content='places', content_rowid='rowid', tokenize='unicode61')")
    con.execute("INSERT INTO places_fts(rowid, folded) SELECT rowid, folded FROM places")
    con.execute("CREATE VIRTUAL TABLE places_rtree USING rtree(id, minLon, maxLon, minLat, maxLat)")
    con.execute("INSERT INTO places_rtree(id,minLon,maxLon,minLat,maxLat) SELECT rowid,lon,lon,lat,lat FROM places")
    con.commit()
    con.close()


def _load_overture(tmp_path):
    db = str(tmp_path / "ov.db")
    _build_overture_db(db)
    os.environ["OVERTURE_DB"] = db
    m = load()
    return m


def test_fold_strips_diacritics():
    m = load()
    assert m._fold("Phở Hòa") == "pho hoa"
    assert m._fold("Đường Lê Lợi") == "duong le loi"
    assert m._fold(None) == ""


def test_overture_query_prefix_and_diacritic_insensitive(tmp_path):
    m = _load_overture(tmp_path)
    # niche business OSM lacks; matched accent-insensitively by name prefix
    r = m._overture_query("kaldivie", 5, 10.78, 106.70)
    assert len(r) == 1 and r[0]["name"] == "Kaldivie Coffee"
    # extra = street + authoritative 2025 ward + province
    assert r[0]["kind"] == "poi" and r[0]["extra"] == "76A Đường Lê Lai, Phường Bến Thành, Thành phố Hồ Chí Minh"
    # folded query "pho hoa" finds the diacritic'd "Phở Hòa Pasteur"
    assert m._overture_query("pho hoa", 5, 10.78, 106.70)[0]["name"] == "Phở Hòa Pasteur"


def test_overture_extra_strips_stale_admin(tmp_path):
    m = _load_overture(tmp_path)
    # Phở Hòa's freeform tail "P. cũ, Tỉnh Kiên Giang" (pre-2025) must be dropped; the
    # authoritative ward/province appended instead.
    r = m._overture_query("pho hoa", 5, 10.78, 106.70)[0]
    assert r["extra"] == "260C Pasteur, Phường Xuân Hòa, Thành phố Hồ Chí Minh"
    assert "Kiên Giang" not in r["extra"]
    assert r["region"] == "Thành phố Hồ Chí Minh" and r["district"] == "Phường Xuân Hòa"


def test_overture_query_ranks_by_proximity_when_biased(tmp_path):
    m = _load_overture(tmp_path)
    # two Highlands: HCMC (near) vs Hanoi (far, higher conf). Bias near HCMC -> HCMC first.
    r = m._overture_query("highlands", 5, 10.78, 106.70)
    assert len(r) == 2 and r[0]["region"] == "Thành phố Hồ Chí Minh"   # proximity beats conf
    # no bias -> fall back to confidence (Hanoi, conf 90, wins)
    r2 = m._overture_query("highlands", 5, None, None)
    assert r2[0]["region"] == "Thành phố Hà Nội"


def test_overture_place_id_and_detail(tmp_path):
    m = _load_overture(tmp_path)
    r = m._overture_query("kaldivie", 5, 10.78, 106.70)[0]
    assert r["osm_id"].startswith("ov:")                       # synthetic place_id
    assert r["phone"] == "+84281234567" and r["website"] == "https://kaldivie.vn"
    d = m._overture_detail(r["osm_id"])                        # resolves back
    assert d and d["name"] == "Kaldivie Coffee" and d["phone"] == "+84281234567"
    assert m._overture_detail("ov:999999") is None             # missing rowid
    assert m._overture_detail("N123") is None                  # not an Overture id


def test_geo_result_surfaces_overture_metadata(tmp_path):
    m = _load_overture(tmp_path)
    r = m._overture_query("kaldivie", 5, 10.78, 106.70)[0]
    g = m._geo_result(r)
    assert g["place_id"].startswith("ov:")
    assert g["formatted_phone_number"] == "+84281234567" and g["website"] == "https://kaldivie.vn"
    # formatted_address uses extra (street + ward + province), not just name + admin
    assert g["formatted_address"] == "Kaldivie Coffee, 76A Đường Lê Lai, Phường Bến Thành, Thành phố Hồ Chí Minh"


def test_overture_nearby_category(tmp_path):
    m = _load_overture(tmp_path)
    # cafes within 5km of the HCMC point -> HCMC Highlands + Kaldivie (both cafe);
    # the Hanoi Highlands (>1000km) and Phở Hòa (restaurant) excluded.
    res = m._overture_nearby(10.78, 106.70, 5000, "cafe", None, 20)
    names = [r["name"] for r in res]
    assert "Kaldivie Coffee" in names and "Phở Hòa Pasteur" not in names
    assert all(r["distance_m"] <= 5000 for r in res)
    assert [r["distance_m"] for r in res] == sorted(r["distance_m"] for r in res)
    assert res[0]["osm_id"].startswith("ov:")
    # type=restaurant -> only the restaurant
    rnames = [r["name"] for r in m._overture_nearby(10.78, 106.70, 20000, "restaurant", None, 20)]
    assert "Phở Hòa Pasteur" in rnames and "Kaldivie Coffee" not in rnames


def test_overture_missing_db_returns_empty(tmp_path):
    os.environ["OVERTURE_DB"] = str(tmp_path / "nope.db")
    m = load()
    assert m._overture_query("anything", 5, 10.78, 106.70) == []
    assert m._overture_nearby(10.78, 106.70, 1500, "cafe", None, 20) == []
