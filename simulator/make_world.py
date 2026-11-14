"""데모 도로망(server/data/world_demo.json)을 만든다.

가로·세로 5개씩 난 바둑판 도로(6km × 6km, 교차로 간격 1.5km)다. 좌표는 경기 남부 어딘가의 가상 위치이고,
길 조각 하나를 방향마다 따로 구간으로 둔다(가로 40 + 세로 40 = 80개). 일부는 굽은 길이라 핸들 각도를 재지 않는다.

  python simulator/make_world.py
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from fleetdoc.geo import LocalFrame, bearing_deg, geohash_encode, haversine_m  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = [ROOT / "server" / "data" / "world_demo.json",
       ROOT / "android" / "app" / "src" / "test" / "resources" / "world_demo.json"]   # 앱 시험도 같은 지도를 쓴다
ORIGIN = (37.2800, 127.0700)
SPACING_M = 1500.0
N = 5                      # 도로 수(가로·세로 각각)
CURVED_SHARE = 0.15
V_REF_KMH = 80.0


def node_xy(i: int, j: int) -> tuple[float, float]:
    return i * SPACING_M, j * SPACING_M


def link_points(frame: LocalFrame, a: tuple[float, float], b: tuple[float, float], curved: bool):
    """직선이면 양 끝 두 점, 굽은 길이면 옆으로 120m 휘는 9점."""
    if not curved:
        pts = [a, b]
    else:
        (ax, ay), (bx, by) = a, b
        nx, ny = -(by - ay) / SPACING_M, (bx - ax) / SPACING_M     # 왼쪽 법선
        pts = []
        for k in range(9):
            t = k / 8
            bump = 120.0 * math.sin(math.pi * t)
            pts.append((ax + (bx - ax) * t + nx * bump, ay + (by - ay) * t + ny * bump))
    return [frame.to_latlon(x, y) for x, y in pts]


def segment(seg_id: str, road: str, pts: list[tuple[float, float]], curved: bool) -> dict:
    length = sum(haversine_m(*p, *q) for p, q in zip(pts, pts[1:]))
    mid = pts[len(pts) // 2]
    return {"id": seg_id, "road": road, "heading": round(bearing_deg(*pts[0], *pts[-1]), 1),
            "straight": not curved, "length_m": round(length, 1), "v_ref_kmh": V_REF_KMH,
            "cell": geohash_encode(mid[0], mid[1], 5),
            "points": [[round(lat, 6), round(lon, 6)] for lat, lon in pts]}


def build(seed: int = 7) -> dict:
    rng = random.Random(seed)
    frame = LocalFrame(*ORIGIN)
    segs = []
    for i in range(N):                       # 세로 도로 V1..V5: 북쪽(N)·남쪽(S) 방향
        for j in range(N - 1):
            curved = rng.random() < CURVED_SHARE
            fwd = link_points(frame, node_xy(i, j), node_xy(i, j + 1), curved)
            segs.append(segment(f"V{i + 1}-{j + 1}N", f"V{i + 1}", fwd, curved))
            segs.append(segment(f"V{i + 1}-{j + 1}S", f"V{i + 1}", fwd[::-1], curved))
    for j in range(N):                       # 가로 도로 H1..H5: 동쪽(E)·서쪽(W) 방향
        for i in range(N - 1):
            curved = rng.random() < CURVED_SHARE
            fwd = link_points(frame, node_xy(i, j), node_xy(i + 1, j), curved)
            segs.append(segment(f"H{j + 1}-{i + 1}E", f"H{j + 1}", fwd, curved))
            segs.append(segment(f"H{j + 1}-{i + 1}W", f"H{j + 1}", fwd[::-1], curved))
    # 경유지 안내 시연용 정비소. 가상의 지도 위 가상의 곳이다(실제 정비소 목록은 출시 때 붙인다)
    service_points = []
    for k, (i, j) in enumerate([(2, 1), (4, 3)]):
        lat, lon = frame.to_latlon(i * SPACING_M + 80.0, j * SPACING_M + 80.0)
        service_points.append({"id": f"SP{k + 1}", "name": f"시연용 정비소 {k + 1}",
                               "lat": round(lat, 6), "lon": round(lon, 6)})
    return {"world_id": "demo-grid-v1", "origin": list(ORIGIN), "spacing_m": SPACING_M, "size": N,
            "segments": segs, "service_points": service_points}


if __name__ == "__main__":
    world = build()
    for out in OUT:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(world, ensure_ascii=False, indent=1), encoding="utf-8")
    straight = sum(s["straight"] for s in world["segments"])
    print(f"world_demo.json: 구간 {len(world['segments'])}개 (똑바른 구간 {straight}개)")
