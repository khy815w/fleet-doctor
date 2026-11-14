"""앱(Kotlin)과 기준 구현(Python)이 같은 계산을 하는지 맞춰 보는 시험 자료를 만든다.

차 한 대가 24일 동안 하루 두 번 출퇴근한다. 14일째부터 핸들이 2.4도 틀어지고, 18일째부터 에너지를 7% 더 쓴다.
매 주행마다 '보고 값 → 주행 판단 → 하루 끝 다시 배우기'를 순서대로 기록하고, 그때마다 상태를 적어 둔다.

  python simulator/make_vectors.py   → server/tests/vectors/carhealth.json (앱 시험도 이 파일을 읽는다)
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from fleetdoc import config  # noqa: E402
from fleetdoc.carmodel import CarHealth  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = [ROOT / "server" / "tests" / "vectors" / "carhealth.json",
       ROOT / "android" / "app" / "src" / "test" / "resources" / "carhealth.json"]
# 앱의 HealthConfig.kt가 같은 값을 가져야 하는 설정. 앱 시험이 이 값들과 비교한다.
CAR_CONFIG = ["LEARN_PASSES", "LEARN_MIN_DAYS", "LEARN_MIN_TRIPS", "REPORT_MIN_PASSES", "RECOMPUTE_EVERY_DAYS",
              "SIGMA_FLOOR", "TAU_FLOOR", "TARGET", "CUSUM_H", "CLIP_SIGMA", "RECENT_TRIPS", "MIN_WINDOW_PASSES",
              "AGREE_TRIPS", "MIN_SHIFT_RATIO", "CLEAR_RATIO", "CLEAR_TRIPS", "CUSUM_CAP_RATIO",
              "MATCH_MAX_OFFSET_M", "MATCH_MAX_HEADING_DIFF", "MIN_COVERAGE", "STEADY_SPEED_TOL_KMH",
              "STEADY_SPEED_SD_KMH", "SPEED_SLOPE_PER_KMH", "STEER_RATIO_NOMINAL", "TRIP_GAP_S", "HEADING_BINS",
              "EPOCH_KST_UNIX"]
SEGS = ["V2-1N", "V2-2N", "V2-3N", "H4-2E", "H4-3E", "V4-3S", "V4-2S", "V4-1S", "H1-3W", "H1-2W"]


def road(metric: str, seg: str, day: int) -> float:
    """시험용 길 몫. 구간과 날짜에 따라 조금씩 다르다."""
    k = SEGS.index(seg)
    if metric == "steer":
        return -0.35 + 0.2 * math.sin(k) + 0.1 * math.cos(day)
    return 0.03 * math.cos(k) + 0.004 * day


def state(h: CarHealth) -> dict:
    out = {}
    for q, t in h.trackers.items():
        out[q] = {"learned": t.learned, "baseline": t.baseline, "sigma": t.sigma, "tau": t.tau,
                  "s_pos": t.s_pos, "s_neg": t.s_neg, "alarm_shift": t.alarm_shift, "calm": t.calm}
    f = h.finding()
    out["finding"] = f.kind if f else None
    out["speech"] = h.speech()
    return out


def build(seed: int = 5) -> dict:
    rng = random.Random(seed)
    h = CarHealth("C", "ev")
    ops = []
    for day in range(40, 64):
        for ti in range(2):
            trip = day * 10 + ti
            segs = SEGS[:5] if ti == 0 else SEGS[5:]
            items = {q: [] for q in config.METRICS}
            reports = []
            for seg in segs:
                if rng.random() < 0.25:
                    continue
                steer = 0.6 + road("steer", seg, day) + rng.gauss(0, 0.4) + (2.4 if day >= 54 else 0.0)
                eff = math.log(0.15) + road("eff", seg, day) + rng.gauss(0, 0.035) + (0.068 if day >= 58 else 0.0)
                rep = {"segment": seg}
                for q, y in (("steer", steer), ("eff", eff)):
                    if q == "steer" and seg.startswith("H4"):
                        continue                    # 이 구간은 굽은 길이라고 치고 핸들 값을 쓰지 않는다
                    rep[q] = {"y": y, "v": h.trackers[q].report_value(y)}
                    items[q].append([seg, day, y, road(q, seg, day)])
                reports.append(rep)
            for q in config.METRICS:
                h.trackers[q].observe_trip(trip, [tuple(x) for x in items[q]])
            ops.append({"op": "trip", "trip": trip, "reports": reports, "items": items, "expect": state(h)})
        for q in config.METRICS:
            t = h.trackers[q]
            if t.needs_refit(day):
                rs = [road(q, s, d) for s, d in t.learn_keys()]
                t.refit(rs, day)
                ops.append({"op": "refit", "metric": q, "day": day, "rs": rs, "expect": state(h)})
    return {"model": "C", "powertrain": "ev", "config": {k: getattr(config, k) for k in CAR_CONFIG},
        "ops": ops}


if __name__ == "__main__":
    data = build()
    for path in OUT:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    last = data["ops"][-1]["expect"]
    print(f"시험 자료 {len(data['ops'])}단계 → 마지막 판단: {last['finding']} / {last['speech']}")
