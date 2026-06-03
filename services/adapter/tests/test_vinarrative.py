"""Vietnamese turn-by-turn templating from Valhalla maneuver types."""
from app.vinarrative import vi_instruction


def test_turn_left_with_street():
    assert vi_instruction({"type": 15, "street_names": ["Lê Thánh Tôn"]}) == "Rẽ trái vào Lê Thánh Tôn"


def test_turn_right_no_street():
    assert vi_instruction({"type": 10}) == "Rẽ phải"


def test_continue_on_street():
    assert vi_instruction({"type": 8, "street_names": ["Nguyễn Huệ"]}) == "Đi thẳng trên Nguyễn Huệ"


def test_uturn():
    assert vi_instruction({"type": 12}) == "Quay đầu"


def test_roundabout_with_exit_count():
    s = vi_instruction({"type": 26, "roundabout_exit_count": 2, "street_names": ["Cách Mạng Tháng Tám"]})
    assert "vòng xoay" in s and "lối ra thứ 2" in s and "Cách Mạng Tháng Tám" in s


def test_destination_right():
    assert vi_instruction({"type": 5}) == "Đã đến nơi bên phải"


def test_unknown_type_falls_back_to_english():
    assert vi_instruction({"type": 99, "instruction": "Bear right"}) == "Bear right"


def test_first_street_name_used_not_duplicate():
    # Valhalla often repeats "X" and "Đường X"; we take the first.
    assert vi_instruction({"type": 15, "street_names": ["Lê Lợi", "Đường Lê Lợi"]}) == "Rẽ trái vào Lê Lợi"


def test_exit_distinct_from_ramp():
    # kExitRight/Left (20/21) say "lối ra"; kRampRight/Left (18/19) stay "đường nhánh"
    assert vi_instruction({"type": 20, "street_names": ["QL1A"]}) == "Đi ra lối ra bên phải vào QL1A"
    assert vi_instruction({"type": 21}) == "Đi ra lối ra bên trái"
    assert vi_instruction({"type": 18}) == "Đi theo đường nhánh bên phải"


def test_stay_straight_uses_continue_preposition():
    # kStayStraight (22) continues ALONG a road -> "trên", not the turn-onto "vào"
    assert vi_instruction({"type": 22, "street_names": ["Võ Văn Kiệt"]}) == "Đi thẳng trên Võ Văn Kiệt"


def test_merge_left_right_covered():
    assert vi_instruction({"type": 37, "street_names": ["CT01"]}) == "Nhập vào làn bên phải vào CT01"
    assert vi_instruction({"type": 38}) == "Nhập vào làn bên trái"
