"""거리·방위·평면 투영·지오해시. 외부 라이브러리 없이 쓴다."""
from __future__ import annotations

import math

EARTH_R = 6_371_000.0
_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return 360.0 - d if d > 180.0 else d


class LocalFrame:
    """기준점 주변을 평면(m)으로 펴는 등장방형 투영. 수십 km 안에서는 오차가 작다."""

    def __init__(self, lat0: float, lon0: float):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = math.radians(1) * EARTH_R * math.cos(math.radians(lat0))
        self.ky = math.radians(1) * EARTH_R

    def to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky

    def to_latlon(self, x: float, y: float) -> tuple[float, float]:
        return self.lat0 + y / self.ky, self.lon0 + x / self.kx


def project_on_segment(px, py, ax, ay, bx, by) -> tuple[float, float]:
    """점 P에서 선분 AB까지의 거리와, 수선의 발이 놓인 위치(0~1)."""
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy)), t


def geohash_encode(lat: float, lon: float, precision: int = 5) -> str:
    lat_rng, lon_rng = [-90.0, 90.0], [-180.0, 180.0]
    out, bits, ch, even = [], 0, 0, True
    while len(out) < precision:
        rng, val = (lon_rng, lon) if even else (lat_rng, lat)
        mid = (rng[0] + rng[1]) / 2
        if val >= mid:
            ch = (ch << 1) | 1
            rng[0] = mid
        else:
            ch <<= 1
            rng[1] = mid
        even = not even
        bits += 1
        if bits == 5:
            out.append(_BASE32[ch])
            bits, ch = 0, 0
    return "".join(out)


def geohash_bbox(gh: str) -> tuple[float, float, float, float]:
    """(위도 최소, 위도 최대, 경도 최소, 경도 최대)."""
    lat_rng, lon_rng = [-90.0, 90.0], [-180.0, 180.0]
    even = True
    for c in gh:
        v = _BASE32.index(c)
        for shift in range(4, -1, -1):
            rng = lon_rng if even else lat_rng
            mid = (rng[0] + rng[1]) / 2
            if (v >> shift) & 1:
                rng[0] = mid
            else:
                rng[1] = mid
            even = not even
    return lat_rng[0], lat_rng[1], lon_rng[0], lon_rng[1]


def geohash_neighbors(gh: str) -> list[str]:
    """자기 칸과 주변 8칸, 모두 9칸."""
    lat_lo, lat_hi, lon_lo, lon_hi = geohash_bbox(gh)
    clat, clon = (lat_lo + lat_hi) / 2, (lon_lo + lon_hi) / 2
    h, w = lat_hi - lat_lo, lon_hi - lon_lo
    return [geohash_encode(clat + dy * h, clon + dx * w, len(gh))
            for dy in (-1, 0, 1) for dx in (-1, 0, 1)]


def is_geohash(s: object, precision: int) -> bool:
    return isinstance(s, str) and len(s) == precision and all(c in _BASE32 for c in s)
