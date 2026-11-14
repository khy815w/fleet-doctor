"""에뮬레이터 시연 운전. 시연 차가 출퇴근 길을 달리며 차량 신호를 시연 서버 통로(POST /v1/demo/feed)로 보낸다.

앱(mock 빌드)은 이 신호를 Pleos SDK 신호처럼 받는다: 위치·진행 방향·속도, 앞바퀴 각도, 지금 1km당 에너지.
값은 seed_server와 같은 세계(같은 seed, 같은 날짜)의 실제 길 몫 + 시연 차의 평소 값 + 고장 + 흔들림이다.
시연 시계는 실제보다 --speedup배 빨리 간다(1초에 표본 여러 개). 7.5km 출근 한 번(도착해 서 있기 포함)이 20초쯤 걸린다.

순서: 평소처럼 출근·퇴근 → (고장이 생긴다) → 출근·퇴근·출근 …
앱이 알리고 "경유지로 넣을까요?"라고 물으면 "응"이라고 대답하고(방송), 그다음 "내 차 괜찮아?"를 묻는다.

  python simulator/sim.py --server http://127.0.0.1:8000          # 서버 채우기 (먼저)
  python simulator/drive_emulator.py --server http://127.0.0.1:8000 --serial emulator-5554
"""
from __future__ import annotations

import argparse
import math
import queue
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from fleet import DEMO_LOOP, Truth, demo_car, load_world  # noqa: E402
from fleetdoc import config  # noqa: E402
from fleetdoc.geo import LocalFrame, angle_diff  # noqa: E402
from fleetdoc.world import today as today_index  # noqa: E402

# 고장: (핸들 몫 변화(도), 에너지 몫 변화(로그))
FAULTS = {
    "tire": (1.8, 0.06),          # 왼쪽 앞 타이어 바람 빠짐: 왼쪽으로 쏠리고 에너지도 더 쓴다
    "alignment": (2.4, 0.0),      # 바퀴 정렬 틀어짐
    "drag": (0.0, 0.08),          # 브레이크 끌림
    "none": (0.0, 0.0),
}
TURN_SLOW_M = 120.0               # 교차로에서 돌기 전후 이만큼은 천천히
TURN_KMH = 35.0
STOP_S = 190                      # 주행 끝에 이만큼 서 있으면 앱이 주행을 끝낸다(앱 기준 180초)
APP = "com.fleetdoctor"


def polyline(seg) -> tuple[LocalFrame, list[tuple[float, float]], list[float]]:
    frame = LocalFrame(*seg.points[0])
    xy = [frame.to_xy(lat, lon) for lat, lon in seg.points]
    cum = [0.0]
    for (ax, ay), (bx, by) in zip(xy, xy[1:]):
        cum.append(cum[-1] + math.hypot(bx - ax, by - ay))
    return frame, xy, cum


def point_at(frame, xy, cum, d: float) -> tuple[float, float, float]:
    """구간 시작부터 d(m) 지점의 위도·경도·진행 방향."""
    d = max(0.0, min(d, cum[-1]))
    i = max(0, min(len(xy) - 2, next((k for k in range(len(cum) - 1) if cum[k + 1] >= d), len(xy) - 2)))
    (ax, ay), (bx, by) = xy[i], xy[i + 1]
    piece = cum[i + 1] - cum[i]
    f = 0.0 if piece == 0 else (d - cum[i]) / piece
    lat, lon = frame.to_latlon(ax + (bx - ax) * f, ay + (by - ay) * f)
    heading = (math.degrees(math.atan2(bx - ax, by - ay)) + 360.0) % 360.0
    return lat, lon, heading


def trip_samples(world, truth, car, day: int, route: list[str], fault: tuple[float, float],
                 rng: random.Random, t0: float) -> list[dict]:
    """주행 한 번(구간들 + 끝에 서 있기)의 1초 간격 표본."""
    m = car.model
    out: list[dict] = []
    t = t0
    for k, seg_id in enumerate(route):
        seg = world.by_id[seg_id]
        frame, xy, cum = polyline(seg)
        length = cum[-1]
        prev = world.by_id[route[k - 1]] if k > 0 else None
        nxt = world.by_id[route[k + 1]] if k + 1 < len(route) else None
        slow_start = prev is None or angle_diff(prev.heading, seg.heading) > 30
        slow_end = nxt is None or angle_diff(nxt.heading, seg.heading) > 30
        # 이 구간을 지나는 동안의 값(시뮬레이터 fleet.py의 drive와 같은 꼴)
        wheel = car.offset + truth.steer(seg_id, m, day) + fault[0] + rng.gauss(0.0, 0.40)
        if rng.random() < 0.06:                                   # 차로 변경이 섞였다
            wheel += rng.gauss(0.0, 1.5)
        eff = math.log(m.base) + car.style + truth.eff(seg_id, m, day) + fault[1] + rng.gauss(0.0, 0.035)
        if rng.random() < 0.10:                                   # 에어컨·히터를 세게 틀었다
            eff += rng.uniform(0.03, 0.12)
        cruise = seg.v_ref_kmh + rng.gauss(0.0, 3.0)
        d = 0.0
        while d < length:
            v = cruise
            if slow_start and d < TURN_SLOW_M:
                v = TURN_KMH + (cruise - TURN_KMH) * d / TURN_SLOW_M
            if slow_end and d > length - TURN_SLOW_M:
                v = TURN_KMH + (cruise - TURN_KMH) * (length - d) / TURN_SLOW_M
            v = max(5.0, v + rng.gauss(0.0, 1.0))
            lat, lon, heading = point_at(frame, xy, cum, d)
            out.append({
                "t": t, "lat": round(lat, 7), "lon": round(lon, 7), "heading": round(heading, 1),
                "speed_kmh": round(v, 2),
                "wheel_deg": round((wheel + rng.gauss(0.0, 1.0)) / config.STEER_RATIO_NOMINAL, 5),   # 앞바퀴 각도
                "consumption": round(math.exp(eff + m.speed_slope * (v - seg.v_ref_kmh) + rng.gauss(0.0, 0.03)), 6),
            })
            t += 1.0
            d += v / 3.6
    last = out[-1]
    for _ in range(STOP_S):                                       # 도착해서 선다
        out.append(dict(last, t=t, speed_kmh=0.0, wheel_deg=0.0, consumption=None))
        t += 1.0
    return out


class App:
    """adb로 앱에 방송을 보내고, 앱 기록(logcat)을 읽는다."""

    def __init__(self, adb: str, serial: str):
        self.adb, self.serial = adb, serial
        self.lines: queue.Queue[str] = queue.Queue()
        self.proc = subprocess.Popen([adb, "-s", serial, "logcat", "-v", "time", "-s", "FleetDoctor:I"],
                                     stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.lines.put(line.rstrip())

    def broadcast(self, action: str, text: str | None = None) -> None:
        cmd = [self.adb, "-s", self.serial, "shell", "am", "broadcast", "-a", f"{APP}.{action}", "-p", APP]
        if text is not None:
            cmd += ["--es", "text", text]
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"  → 방송 {action}" + (f" ({text})" if text else ""), flush=True)

    def close(self) -> None:
        self.proc.terminate()


def main() -> None:
    ap = argparse.ArgumentParser(description="에뮬레이터 시연 운전")
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--serial", default="emulator-5554")
    ap.add_argument("--adb", default=str(Path.home() / "Library/Android/sdk/platform-tools/adb"))
    ap.add_argument("--seed", type=int, default=3, help="seed_server와 같은 값")
    ap.add_argument("--seed-days", type=int, default=30, help="seed_server와 같은 값")
    ap.add_argument("--trips", type=int, default=6)
    ap.add_argument("--fault", choices=list(FAULTS), default="tire")
    ap.add_argument("--fault-from", type=int, default=2, help="이 번째 주행부터 고장(0부터)")
    ap.add_argument("--speedup", type=float, default=25.0, help="시연 시계가 실제보다 몇 배 빠른가")
    ap.add_argument("--answer", default="응", help="'경유지로 넣을까요?'에 할 대답")
    args = ap.parse_args()

    world = load_world()
    today = today_index()
    day0 = today - args.seed_days
    truth = Truth(world, args.seed, day0=day0, days=args.seed_days + 1)    # seed_server와 같은 세계
    car = demo_car(args.seed + 1000)
    rng = random.Random(args.seed * 7 + today)
    client = httpx.Client(base_url=args.server, timeout=10)
    app = App(args.adb, args.serial)
    answered = asked = False
    routes = [DEMO_LOOP[:5], DEMO_LOOP[5:]]                       # 출근, 퇴근

    def react() -> None:
        nonlocal answered, asked
        while True:
            try:
                line = app.lines.get_nowait()
            except queue.Empty:
                return
            if "speak:" in line or "trip " in line or "heard:" in line or "waypoint:" in line:
                print("  [앱] " + line.split(": ", 1)[-1], flush=True)
            if "listening" in line and not answered:
                answered = True
                time.sleep(1.5)
                app.broadcast("ANSWER", args.answer)
            if "경유지로 넣었어요" in line and not asked:
                asked = True
                time.sleep(2.0)
                app.broadcast("ASK")

    t = 0.0
    batch = max(1, int(args.speedup / 5))                         # 0.2초마다 이만큼
    for k in range(args.trips):
        fault = FAULTS[args.fault] if k >= args.fault_from else FAULTS["none"]
        route = routes[k % 2]
        samples = trip_samples(world, truth, car, today, route, fault, rng, t)
        t = samples[-1]["t"] + 1.0
        print(f"주행 {k + 1}/{args.trips}: {' → '.join(route)}  고장: {args.fault if fault != FAULTS['none'] else '없음'}"
              f"  (표본 {len(samples)}개)", flush=True)
        for i in range(0, len(samples), batch):
            client.post("/v1/demo/feed", json={"samples": samples[i:i + batch]}).raise_for_status()
            react()
            time.sleep(0.2)
    deadline = time.time() + 40                                   # 마지막 주행 판단·대답·물음까지 기다린다
    while time.time() < deadline and not (asked and time.time() > deadline - 25):
        react()
        time.sleep(0.5)
    react()
    app.close()
    print("끝" + ("" if answered else " (앱이 경유지를 묻지 않았다)"), flush=True)


if __name__ == "__main__":
    main()
