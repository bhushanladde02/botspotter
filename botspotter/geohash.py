"""Tiny geohash encoder (no dependency) for pre-aggregated map buckets.

Precision -> cell size: 2 ≈ 1250km, 3 ≈ 156km, 4 ≈ 39km, 5 ≈ 5km.
The aggregator keeps counts at precisions 2, 3 and 4; the map picks one
by zoom level. Lower precision for country-centroid data (no city info).
"""
from __future__ import annotations

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def encode(lat: float, lon: float, precision: int = 4) -> str:
    lat_i = (-90.0, 90.0)
    lon_i = (-180.0, 180.0)
    bits = 0
    bit = 0
    even = True
    out = []
    while len(out) < precision:
        if even:
            mid = (lon_i[0] + lon_i[1]) / 2
            if lon > mid:
                bits = (bits << 1) | 1
                lon_i = (mid, lon_i[1])
            else:
                bits <<= 1
                lon_i = (lon_i[0], mid)
        else:
            mid = (lat_i[0] + lat_i[1]) / 2
            if lat > mid:
                bits = (bits << 1) | 1
                lat_i = (mid, lat_i[1])
            else:
                bits <<= 1
                lat_i = (lat_i[0], mid)
        even = not even
        bit += 1
        if bit == 5:
            out.append(_BASE32[bits])
            bits = 0
            bit = 0
    return "".join(out)


def decode(gh: str) -> tuple[float, float]:
    lat_i = [-90.0, 90.0]
    lon_i = [-180.0, 180.0]
    even = True
    for ch in gh:
        cd = _BASE32.index(ch)
        for mask in (16, 8, 4, 2, 1):
            interval = lon_i if even else lat_i
            mid = (interval[0] + interval[1]) / 2
            if cd & mask:
                interval[0] = mid
            else:
                interval[1] = mid
            even = not even
    return (lat_i[0] + lat_i[1]) / 2, (lon_i[0] + lon_i[1]) / 2
