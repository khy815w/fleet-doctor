"""플릿 닥터 시뮬레이터.

차종 6가지가 섞인 차 수백 대가 가상 도로망을 70일 동안 출퇴근하고, 그중 10%에 고장을 넣는다.
차마다 앱의 판단 로직(fleetdoc.carmodel)을 그대로 돌리고, 길 몫을 구하는 방식만 바꿔 견준다.

  python simulator/sim.py                     # 600대, 방식 4가지 비교 → simulator/results/compare.json
  python simulator/sim.py --cars 150          # 차가 적을 때
  python simulator/sim.py --server http://127.0.0.1:8000
        # 서버에 지난 30일과 오늘의 익명 보고를 채우고, 에뮬레이터 시연 차의 지난 기록을 올린다
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from fleet import FAULT_KINDS, MODELS, RIGHT_ADVICE, Fleet, Truth, demo_car, load_world  # noqa: E402
from fleetdoc import config  # noqa: E402
from fleetdoc.carmodel import CarHealth  # noqa: E402
from fleetdoc.effects import EffectStore  # noqa: E402
from fleetdoc.world import today as today_index  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"
STRATEGIES = {
    "own": "내 차 평소 값만 (다른 차와 비교 안 함)",
    "fleet_all": "다른 차와 비교, 차종 무시",
    "fleet_model": "다른 차와 비교, 차종 반영 (플릿 닥터)",
    "oracle": "실제 길 몫을 그대로 안다고 칠 때 (참고)",
}
DETECT_WINDOW_DAYS = 21
MODEL_IDS = [m.id for m in MODELS]


class EffectSource:
    """방식마다 길 몫 r을 주는 곳."""

    def __init__(self, kind: str, world, truth):
        self.kind, self.world, self.truth = kind, world, truth
        self.store = EffectStore(world, MODEL_IDS, model_aware=(kind == "fleet_model")) \
            if kind.startswith("fleet") else None

    def lookup(self, metric: str, segs: list[str], model, days) -> np.ndarray:
        if self.kind == "own":
            return np.zeros(len(segs))
        if self.kind == "oracle":
            days = days if isinstance(days, list) else [days] * len(segs)
            f = self.truth.steer if metric == "steer" else self.truth.eff
            return np.array([f(s, model, d) for s, d in zip(segs, days)])
        idx = [self.world.by_id[s].idx for s in segs]
        return self.store.lookup(metric, idx, MODEL_IDS.index(model.id), days)


def step_day(fleet: Fleet, health: list[CarHealth], cars, src: EffectSource, day: int, post=None):
    """하루: 주행 → 익명 보고 → 서버 추정 → 차마다 판단·다시 배우기."""
    passes = [fleet.drive(c, day) for c in cars]
    if src.store is not None or post is not None:
        rows = {q: ([], [], []) for q in config.METRICS}
        by_model: dict[str, list[dict]] = {}
        for c, h, ps in zip(cars, health, passes):
            for p in ps:
                item = {"segment": p.seg}
                for q in config.METRICS:
                    y = getattr(p, q)
                    v = h.trackers[q].report_value(y) if y is not None else None
                    if v is not None:
                        item[q] = round(v, 5)
                        rows[q][0].append(fleet.world.by_id[p.seg].idx)
                        rows[q][1].append(MODEL_IDS.index(c.model.id))
                        rows[q][2].append(v)
                if len(item) > 1:
                    by_model.setdefault(c.model.id, []).append(item)
        if src.store is not None:
            for q, (s, m, v) in rows.items():
                src.store.add(q, s, m, day, v)
            src.store.fit(day)
        if post is not None:
            post(day, by_model)
    for c, h, ps in zip(cars, health, passes):
        for q in config.METRICS:
            t = h.trackers[q]
            items = [p for p in ps if getattr(p, q) is not None]
            if items:
                rs = src.lookup(q, [p.seg for p in items], c.model, day)
                trips: dict[int, list] = {}
                for p, r in zip(items, rs):
                    trips.setdefault(p.trip, []).append((p.seg, day, getattr(p, q), float(r)))
                for trip_id, rows in trips.items():
                    t.observe_trip(trip_id, rows)
            if t.needs_refit(day):
                keys = t.learn_keys()
                rs = src.lookup(q, [s for s, _ in keys], c.model, [d for _, d in keys])
                t.refit([float(r) for r in rs], day)
    return passes


def evaluate(fleet: Fleet, src: EffectSource) -> dict:
    cars, truth = fleet.cars, fleet.truth
    health = [CarHealth(c.model.id, c.model.powertrain) for c in cars]
    findings = [[None] * truth.days for _ in cars]
    learned_day = [None] * len(cars)
    for k in range(truth.days):
        day = truth.day0 + k
        step_day(fleet, health, cars, src, day)
        for i, h in enumerate(health):
            f = h.finding()
            findings[i][k] = f.kind if f else None
            if learned_day[i] is None and all(t.learned for t in h.trackers.values()):
                learned_day[i] = k

    false_onsets = {"ev": 0, "ice": 0}
    monitored = {"ev": 0, "ice": 0}
    per_fault = {kind: [] for kind in FAULT_KINDS}
    for i, c in enumerate(cars):
        pt = c.model.powertrain
        start = c.fault.start - truth.day0 if c.fault else truth.days
        for k in range(truth.days):
            if k >= start or learned_day[i] is None or k < learned_day[i]:
                continue
            monitored[pt] += 1
            if findings[i][k] and (k == 0 or not findings[i][k - 1]):
                false_onsets[pt] += 1
        if c.fault:
            # 고장 뒤 '새로 울린' 알림만 찾은 것으로 센다. 오경보가 이미 켜져 있던 차는 새 정보가 없으니 못 찾은 것으로 친다.
            hit = next((k for k in range(start, min(truth.days, start + DETECT_WINDOW_DAYS))
                        if findings[i][k] and (k == 0 or not findings[i][k - 1])), None)
            per_fault[c.fault.kind].append({
                "car": c.idx, "model": c.model.id, "start": start,
                "already_alarming": start > 0 and findings[i][start - 1] is not None,
                "detected_after_days": None if hit is None else hit - start,
                "kind_found": None if hit is None else findings[i][hit],
                "steer": round(c.fault.steer, 2), "eff_pct": round(100 * c.fault.eff, 1),
                "tpms_after_days": None if c.fault.tpms_day is None else c.fault.tpms_day - c.fault.start,
                "learned": learned_day[i] is not None and learned_day[i] <= start,
            })

    def summary(rows):
        usable = [r for r in rows if r["learned"]]
        hits = [r for r in usable if r["detected_after_days"] is not None]
        out = {"faults": len(usable), "detected": len(hits),
               "detected_share": round(len(hits) / len(usable), 3) if usable else None,
               "median_days": statistics.median(r["detected_after_days"] for r in hits) if hits else None,
               "right_advice": sum(r["kind_found"] in RIGHT_ADVICE[r["kind"]] for r in hits) if hits else 0,
               "already_alarming": sum(r["already_alarming"] for r in usable)}
        leads = [r["tpms_after_days"] - r["detected_after_days"] for r in hits if r["tpms_after_days"] is not None]
        if leads:
            out["days_before_tpms_median"] = statistics.median(leads)
        return out

    for kind, rows in per_fault.items():
        for r in rows:
            r["kind"] = kind
    all_rows = [r for rows in per_fault.values() for r in rows]
    total_mon = sum(monitored.values())
    found = sum(1 for r in all_rows if r["learned"] and r["detected_after_days"] is not None)
    false_total = sum(false_onsets.values())
    return {
        "alarm_precision": round(found / max(found + false_total, 1), 3),
        "false_alarms": sum(false_onsets.values()),
        "monitored_car_days": total_mon,
        "false_per_1000_car_days": round(1000 * sum(false_onsets.values()) / max(total_mon, 1), 2),
        "false_per_1000_by_powertrain": {pt: round(1000 * false_onsets[pt] / max(monitored[pt], 1), 2)
                                         for pt in monitored},
        "all": summary(all_rows),
        "by_kind": {kind: summary(rows) for kind, rows in per_fault.items()},
        "learned_cars": sum(d is not None for d in learned_day),
        "median_learn_days": statistics.median(d for d in learned_day if d is not None)
        if any(d is not None for d in learned_day) else None,
        "faults": all_rows,
    }



def run_compare(n_cars: int, days: int, seed: int, strategies: list[str]) -> dict:
    world = load_world()
    truth = Truth(world, seed, day0=0, days=days)
    out = {"cars": n_cars, "days": days, "seed": seed, "strategies": {}}
    for name in strategies:
        t0 = time.time()
        fleet = Fleet(world, truth, n_cars, seed)
        res = evaluate(fleet, EffectSource(name, world, truth))
        res["label"] = STRATEGIES[name]
        out["strategies"][name] = res
        a = res["all"]
        print(f"{STRATEGIES[name]:<34} 고장 {a['detected']}/{a['faults']} 찾음 (중앙 {a['median_days']}일), "
              f"알림 중 진짜 고장 {round(100 * res['alarm_precision'])}%, "
              f"오경보 {res['false_alarms']}건 = 1,000대·일당 {res['false_per_1000_car_days']} "
              f"(전기 {res['false_per_1000_by_powertrain']['ev']}, 내연 {res['false_per_1000_by_powertrain']['ice']})"
              f"  [{time.time() - t0:.0f}초]", flush=True)
    return out


# ── 서버 채우기 (에뮬레이터 시연 준비) ──────────────────────────────────
def seed_server(server: str, n_cars: int, past_days: int, seed: int) -> None:
    import httpx

    world = load_world()
    today = today_index()
    day0 = today - past_days
    truth = Truth(world, seed, day0=day0, days=past_days + 1)
    fleet = Fleet(world, truth, n_cars, seed)
    src = EffectSource("fleet_model", world, truth)            # 서버와 같은 계산을 시뮬레이터 안에서도 한다
    health = [CarHealth(c.model.id, c.model.powertrain) for c in fleet.cars]
    client = httpx.Client(base_url=server, timeout=30)
    sent = 0

    def post(day, by_model):
        nonlocal sent
        for model, items in by_model.items():
            for i in range(0, len(items), 2000):
                r = client.post("/v1/observations", json={"model": model, "day": day, "items": items[i:i + 2000]})
                r.raise_for_status()
                sent += r.json()["accepted"]

    demo = demo_car(seed + 1000)
    demo_health = CarHealth(demo.model.id, demo.model.powertrain)
    history = []
    for k in range(past_days + 1):
        day = day0 + k
        # 시연 차도 다른 차들과 함께 달리고 익명 보고를 보낸다. 오늘은 에뮬레이터에서 직접 달린다.
        with_demo = day < today
        cars = fleet.cars + [demo] if with_demo else fleet.cars
        hs = health + [demo_health] if with_demo else health
        passes = step_day(fleet, hs, cars, src, day, post=post)
        if with_demo:
            history += [{"segment": p.seg, "day": day, "trip": p.trip, "steer": p.steer, "eff": p.eff}
                        for p in passes[-1] if p.steer is not None or p.eff is not None]
    r = client.post("/v1/demo/history", json={"model": demo.model.id, "powertrain": demo.model.powertrain,
                                              "passes": history})
    r.raise_for_status()
    st = demo_health.trackers
    print(f"서버에 익명 보고 {sent}건 (차 {n_cars}대, {day0}~{today}일). 시연 차 기록 {len(history)}건 → "
          f"핸들 평소값 {st['steer'].baseline:+.2f}도 (배움 {st['steer'].learned}), "
          f"알림: {demo_health.speech()}")


def main() -> None:
    ap = argparse.ArgumentParser(description="플릿 닥터 시뮬레이터")
    ap.add_argument("--cars", type=int, default=600)
    ap.add_argument("--days", type=int, default=70)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--only", nargs="*", choices=list(STRATEGIES), help="이 방식만")
    ap.add_argument("--out", help="결과 파일 이름 (기본 compare.json, --cars를 바꾸면 compare_<대수>.json)")
    ap.add_argument("--server", help="이 서버에 시연용 보고를 채운다")
    ap.add_argument("--seed-days", type=int, default=30)
    args = ap.parse_args()
    if args.server:
        seed_server(args.server, args.cars, args.seed_days, args.seed)
        return
    res = run_compare(args.cars, args.days, args.seed, args.only or list(STRATEGIES))
    RESULTS.mkdir(exist_ok=True)
    name = args.out or ("compare.json" if args.cars == 600 else f"compare_{args.cars}.json")
    (RESULTS / name).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {RESULTS / name}")


if __name__ == "__main__":
    main()
