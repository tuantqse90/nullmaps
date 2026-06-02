"""Polyline encode/decode.

Valhalla returns encoded polylines at precision 6; Google's APIs use precision 5.
We decode Valhalla's shape and re-encode at precision 5 for Google compatibility.
"""
from __future__ import annotations


def decode(encoded: str, precision: int = 6) -> list[tuple[float, float]]:
    """Decode an encoded polyline string to a list of (lat, lon)."""
    factor = float(10 ** precision)
    coords: list[tuple[float, float]] = []
    index = lat = lon = 0
    length = len(encoded)
    while index < length:
        for is_lon in (False, True):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if is_lon:
                lon += delta
            else:
                lat += delta
        coords.append((lat / factor, lon / factor))
    return coords


def _encode_value(value: int, out: list[str]) -> None:
    value = ~(value << 1) if value < 0 else (value << 1)
    while value >= 0x20:
        out.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    out.append(chr(value + 63))


def encode(coords: list[tuple[float, float]], precision: int = 5) -> str:
    """Encode (lat, lon) pairs to an encoded polyline at the given precision."""
    factor = 10 ** precision
    out: list[str] = []
    prev_lat = prev_lon = 0
    for lat, lon in coords:
        ilat = round(lat * factor)
        ilon = round(lon * factor)
        _encode_value(ilat - prev_lat, out)
        _encode_value(ilon - prev_lon, out)
        prev_lat, prev_lon = ilat, ilon
    return "".join(out)


def reencode(shape6: str) -> str:
    """Valhalla polyline6 -> Google polyline5."""
    return encode(decode(shape6, precision=6), precision=5)
