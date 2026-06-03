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
