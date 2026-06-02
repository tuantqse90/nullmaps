"""Pure-function tests for the geocoder (no DB / no TestClient).

Run: cd services/geocoder && pip install pytest && PYTHONPATH=. pytest -q
"""
import pytest

from app.main import fold, fts_match


def test_fold_strips_vietnamese_diacritics():
    assert fold("Nguyễn Huệ") == "nguyen hue"
    assert fold("Bến Thành") == "ben thanh"
    assert fold("Đà Nẵng") == "da nang"     # đ/Đ -> d
    assert fold("  Hà Nội ") == "ha noi"     # trimmed + lowercased


def test_fold_matches_unaccented_input():
    assert fold("nguyen hue") == fold("Nguyễn Huệ")


def test_fts_match_builds_prefix_query():
    assert fts_match("ben thanh") == '"ben"* "thanh"*'
    assert fts_match("Nguyễn") == '"nguyen"*'
    assert fts_match("   ") == ""


def test_importer_and_service_fold_agree():
    pytest.importorskip("osmium")  # importer.py imports osmium; only present in the geocoder image / CI
    # the index and the query path must fold identically or matches break
    from importer import fold as ifold
    for s in ("Nguyễn Huệ", "Đường Lê Lợi", "Quận 1"):
        assert ifold(s) == fold(s)
