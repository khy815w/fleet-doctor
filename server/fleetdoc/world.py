"""도로 구간. 구간은 방향이 있는 길 조각(1~3km)이고, 길 몫은 구간마다 따로 잡는다.

차는 위치를 구간에 맞춰(맵매칭) '이 구간을 지났다'를 알고, 서버는 구간 ID로만 보고를 받는다.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

from . import config
from .geo import LocalFrame, angle_diff, geohash_encode, project_on_segment


def day_of(unix_s: float) -> int:
    """날짜 번호. 2026-10-01(KST)이 0이다."""
    return int((unix_s - config.EPOCH_KST_UNIX) // 86400)


def today() -> int:
    return day_of(time.time())


def heading_bin(heading: float) -> int:
    """진행 방향을 8방위로. 0=북, 1=북동, 2=동 ..."""
    width = 360.0 / config.HEADING_BINS
    return int(((heading % 360.0) + width / 2) // width) % config.HEADING_BINS


@dataclass
class Segment:
    id: str
    idx: int
    road: str
    heading: float           # 시작점 → 끝점 방위(도)
    straight: bool           # 핸들 각도는 똑바른 구간에서만 잰다
    length_m: float
    v_ref_kmh: float         # 이 구간의 기준 속도. 에너지 값은 이 속도로 보정한다
    points: list[tuple[float, float]]
    cell: str                # 가운데 지점의 지오해시(5자리, 약 5km 칸)

    @property
    def hbin(self) -> int:
        return heading_bin(self.heading)

    def to_dict(self) -> dict:
        return {"id": self.id, "road": self.road, "heading": round(self.heading, 1),
                "straight": self.straight, "length_m": round(self.length_m, 1),
                "v_ref_kmh": self.v_ref_kmh, "cell": self.cell,
                "points": [[round(a, 6), round(b, 6)] for a, b in self.points]}


class World:
    def __init__(self, world_id: str, origin: tuple[float, float], segments: list[Segment],
                 service_points: list[dict] | None = None):
        self.world_id = world_id
        self.origin = origin
        self.segments = segments
        self.service_points = service_points or []     # 정비소 위치(경유지 안내용). 시연 지도에는 가상의 곳만 있다
        self.by_id = {s.id: s for s in segments}
        self.frame = LocalFrame(*origin)
        self._xy = [[self.frame.to_xy(lat, lon) for lat, lon in s.points] for s in segments]

    @classmethod
    def from_dict(cls, d: dict) -> "World":
        segs = []
        for i, s in enumerate(d["segments"]):
            segs.append(Segment(id=s["id"], idx=i, road=s["road"], heading=float(s["heading"]),
                                straight=bool(s["straight"]), length_m=float(s["length_m"]),
                                v_ref_kmh=float(s["v_ref_kmh"]),
                                points=[(float(a), float(b)) for a, b in s["points"]],
                                cell=s.get("cell") or ""))
        w = cls(d["world_id"], tuple(d["origin"]), segs, d.get("service_points"))
        for s in w.segments:
            if not s.cell:
                mid = s.points[len(s.points) // 2]
                s.cell = geohash_encode(mid[0], mid[1], 5)
        return w

    @classmethod
    def load(cls, path: str | Path) -> "World":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_dict(self) -> dict:
        return {"world_id": self.world_id, "origin": list(self.origin),
                "heading_bins": config.HEADING_BINS,
                "segments": [s.to_dict() for s in self.segments],
                "service_points": self.service_points}

    def in_cells(self, cells: set[str]) -> list[Segment]:
        return [s for s in self.segments if s.cell in cells]

    def match(self, lat: float, lon: float, heading: float) -> tuple[Segment, float] | None:
        """가장 가까운 같은 방향 구간과, 구간 시작점부터 지난 거리(m)."""
        px, py = self.frame.to_xy(lat, lon)
        best = None
        for seg, xy in zip(self.segments, self._xy):
            along = 0.0
            for (ax, ay), (bx, by) in zip(xy, xy[1:]):
                piece = math.hypot(bx - ax, by - ay)
                dist, t = project_on_segment(px, py, ax, ay, bx, by)
                piece_heading = (math.degrees(math.atan2(bx - ax, by - ay)) + 360.0) % 360.0
                if dist <= config.MATCH_MAX_OFFSET_M and \
                        angle_diff(piece_heading, heading) <= config.MATCH_MAX_HEADING_DIFF:
                    if best is None or dist < best[0]:
                        best = (dist, seg, along + t * piece)
                along += piece
        return None if best is None else (best[1], best[2])
