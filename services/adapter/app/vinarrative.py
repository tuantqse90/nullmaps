"""Vietnamese turn-by-turn narrative from Valhalla maneuvers.

The bundled Valhalla image has no `vi-VN` Odin locale (fr-FR/de-DE work, vi falls
back to English). This templates the common maneuver types into Vietnamese from the
maneuver `type` + `street_names` (+ `roundabout_exit_count`). Unknown/uncovered types
fall back to Valhalla's own `instruction` (English) so there is always text.
"""
from __future__ import annotations


def _street(m: dict) -> str:
    names = m.get("street_names") or []
    return names[0] if names else ""


def vi_instruction(m: dict) -> str:
    t = m.get("type")
    s = _street(m)
    onto = f" vào {s}" if s else ""   # turning ONTO a street
    on = f" trên {s}" if s else ""    # continuing ON a street
    if t in (1, 2, 3):                # start
        return f"Khởi hành{on}"
    if t in (4, 5, 6):                # destination
        side = " bên phải" if t == 5 else " bên trái" if t == 6 else ""
        return f"Đã đến nơi{side}"
    if t == 7:                        # becomes
        return f"Tiếp tục{on}"
    if t == 8:                        # continue
        return f"Đi thẳng{on}"
    if t == 9:                        # slight right
        return f"Rẽ nhẹ sang phải{onto}"
    if t == 10:                       # right
        return f"Rẽ phải{onto}"
    if t == 11:                       # sharp right
        return f"Rẽ gấp sang phải{onto}"
    if t in (12, 13):                 # u-turn
        return f"Quay đầu{onto}"
    if t == 14:                       # sharp left
        return f"Rẽ gấp sang trái{onto}"
    if t == 15:                       # left
        return f"Rẽ trái{onto}"
    if t == 16:                       # slight left
        return f"Rẽ nhẹ sang trái{onto}"
    if t == 17:                       # ramp straight
        return "Đi thẳng theo đường nhánh"
    if t == 18:                       # ramp right
        return f"Đi theo đường nhánh bên phải{onto}"
    if t == 19:                       # ramp left
        return f"Đi theo đường nhánh bên trái{onto}"
    if t == 20:                       # exit right (leave a highway via an exit)
        return f"Đi ra lối ra bên phải{onto}"
    if t == 21:                       # exit left
        return f"Đi ra lối ra bên trái{onto}"
    if t == 22:                       # stay straight (continue along, not onto a new road)
        return f"Đi thẳng{on}"
    if t == 23:                       # keep right
        return f"Giữ làn bên phải{onto}"
    if t == 24:                       # keep left
        return f"Giữ làn bên trái{onto}"
    if t == 25:                       # merge
        return f"Nhập làn{onto}"
    if t == 26:                       # roundabout enter
        n = m.get("roundabout_exit_count")
        exit_ = f", đi lối ra thứ {n}" if n else ""
        return f"Vào vòng xoay{exit_}{onto}"
    if t == 27:                       # roundabout exit
        return f"Ra khỏi vòng xoay{onto}"
    if t == 28:                       # ferry enter
        return f"Lên phà{on}"
    if t == 29:                       # ferry exit
        return f"Xuống phà{on}"
    if t == 37:                       # merge right
        return f"Nhập vào làn bên phải{onto}"
    if t == 38:                       # merge left
        return f"Nhập vào làn bên trái{onto}"
    return m.get("instruction", "")   # fallback: Valhalla's English
