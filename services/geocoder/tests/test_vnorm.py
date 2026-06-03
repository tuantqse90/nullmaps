"""Pure tests for VN query normalization + trigram helper (no DB)."""
from app.vnorm import normalize_query, trigrams


def test_strip_leading_house_number():
    assert normalize_query("123 nguyen hue") == "nguyen hue"
    assert normalize_query("12a le loi") == "le loi"


def test_expand_quan_phuong_with_digit():
    assert normalize_query("q1") == "quan 1"
    assert normalize_query("q.1 nguyen hue") == "quan 1 nguyen hue"
    assert normalize_query("p3") == "phuong 3"


def test_does_not_touch_bare_letters_in_names():
    assert normalize_query("phu nhuan") == "phu nhuan"   # bare 'p', no digit
    assert normalize_query("quan an ngon") == "quan an ngon"


def test_city_markers_and_street_prefix():
    assert normalize_query("tp hcm") == "thanh pho hcm"
    assert normalize_query("tx di an") == "thi xa di an"
    assert normalize_query("duong le loi") == "le loi"
    assert normalize_query("d. le loi") == "le loi"


def test_trigrams_overlap_catches_typo():
    a, b = trigrams("nguyn hue"), trigrams("nguyen hue")
    shared = len(a & b)
    jac = shared / len(a | b)
    assert jac >= 0.5            # internal typo still highly similar
    assert not (trigrams("q1") & trigrams("nguyen hue"))  # unrelated -> no overlap


def test_fts_match_uses_normalization():
    from app.main import fts_match
    # q1 expands to "quan 1"; existing behavior for plain queries is unchanged
    assert fts_match("q1") == '"quan"* "1"*'
    assert fts_match("ben thanh") == '"ben"* "thanh"*'
