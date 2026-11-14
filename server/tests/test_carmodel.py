"""차 안 판단 로직: 배우기, 꾸준한 변화만 알리기, 알림 문장."""
import json
import math
import re
from pathlib import Path

import pytest
from conftest import drive

from fleetdoc import config
from fleetdoc.carmodel import CarHealth, Finding, describe

VECTORS = Path(__file__).resolve().parent / "vectors" / "carhealth.json"


def test_learns_own_baseline_and_noise():
    h = drive(CarHealth("C"), range(14))
    st = h.trackers["steer"]
    assert st.learned and h.trackers["eff"].learned
    assert st.baseline == pytest.approx(0.5, abs=0.15)          # 차마다 다른 평소 값(센서 0점, 차종 차이)을 그대로 배운다
    assert st.sigma == pytest.approx(0.4, abs=0.12)
    assert h.finding() is None
    assert h.speech() == "핸들 쏠림도, 전기 쓰는 양도 평소와 같아요."


def test_not_learned_before_enough_days_even_with_many_passes():
    h = drive(CarHealth("C"), range(5), trips_per_day=6)        # 5일 동안 120번 지나도
    assert not h.trackers["steer"].learned                      # 10일이 안 됐으니 아직 배우는 중
    assert h.speech().startswith("아직 평소 상태를 배우는 중이에요. 5일 더 타면")


def test_pull_found_within_a_few_trips_and_message_size():
    h = drive(CarHealth("C"), range(30), steer_shift=(20, 2.4))
    f = h.finding()
    assert f is not None and f.kind == "pull"
    assert f.steer_shift == pytest.approx(2.4, abs=0.4)
    assert h.speech().startswith("차가 왼쪽으로 쏠리고 있어요. 똑바로 가려면 핸들을 평소보다 오른쪽으로 약 2도")


def test_pull_alarm_comes_by_the_third_trip():
    """판단용 값은 주행마다 4σ까지만 쌓이므로, 큰 고장도 최근 4번 평균이 기준을 넘는 세 번째 주행에서 알린다."""
    h = drive(CarHealth("C"), range(20))
    h = drive(h, [20], steer_shift=(20, 2.4), seed=9)
    assert h.trackers["steer"].alarm_shift is None              # 두 번째 주행까지는 아직
    h = drive(h, [21], trips_per_day=1, steer_shift=(20, 2.4), seed=9)
    assert h.trackers["steer"].alarm_shift is not None          # 다음 날 첫 주행이면 알린다


def test_one_odd_trip_is_not_enough():
    """차로 변경이 섞인 주행 한 번은 크게 튀어도 알리지 않는다."""
    h = drive(CarHealth("C"), range(16))
    t = h.trackers["steer"]
    t.observe_trip(9990, [("S0", 16, 0.5 + 3.0, 0.0)])
    assert t.alarm_shift is None
    for k in range(3):
        t.observe_trip(9991 + k, [(f"S{i}", 16, 0.5, 0.0) for i in range(3)])
    assert t.alarm_shift is None


def test_energy_drag_and_percent():
    for seed in range(1, 6):
        h = drive(CarHealth("C"), range(30), eff_shift=(20, math.log(1.08)), seed=seed)
        f = h.finding()
        assert f is not None and f.kind == "drag"
        m = re.search(r"전기를 평소보다 약 (\d+)퍼센트 더 쓰고 있어요", h.speech())
        assert m and 5 <= int(m.group(1)) <= 11                  # 최근 4번 주행으로 잡으니 ±2%p쯤 흔들린다


def test_energy_drop_is_not_a_fault():
    h = drive(CarHealth("C"), range(30), eff_shift=(20, math.log(0.9)))
    assert h.finding() is None                                   # 덜 쓰는 것은 고장 신호가 아니다


def test_pull_with_energy_points_to_tire_first():
    h = drive(CarHealth("E", "ice"), range(30), steer_shift=(20, -1.6), eff_shift=(20, 0.06))
    f = h.finding()
    assert f.kind == "tire"
    assert h.speech().startswith("차가 오른쪽으로 쏠리고, 기름도 평소보다")


def test_road_share_is_removed():
    """길 몫이 구간·날짜마다 커도, 그만큼을 빼면 알릴 것이 없다."""
    r = lambda q, seg, day: (0.8 if seg == "S1" else -0.6) if q == "steer" else 0.01 * day
    h = drive(CarHealth("C"), range(30), r=r)
    assert h.finding() is None


def test_suspicious_car_stops_reporting():
    h = drive(CarHealth("C"), range(30), steer_shift=(20, 2.4))
    assert h.trackers["steer"].report_value(3.0) is None         # 고장 의심 차는 길 몫 계산에 끼지 않는다
    assert h.trackers["eff"].report_value(-1.9) is not None


def test_alarm_clears_after_repair_and_reset_relearns():
    h = drive(CarHealth("C"), range(25), steer_shift=(18, 2.4))
    assert h.trackers["steer"].alarm_shift is not None
    h = drive(h, range(25, 32), seed=4)                          # 정비해서 원래대로
    assert h.trackers["steer"].alarm_shift is None
    h.trackers["steer"].reset()
    assert not h.trackers["steer"].learned and h.trackers["steer"].baseline is None


def test_describe_rounding():
    f = Finding("pull", -1.2, 0.0)
    assert "약 1.2도" in describe(f, "ev") and "차가 오른쪽으로" in describe(f, "ev")
    assert "약 3도" in describe(Finding("pull", 2.6, 0.0), "ev")


def test_vectors_replay():
    """앱(Kotlin)과 같은 시험 자료를 기준 구현으로 다시 돌려 본다."""
    data = json.loads(VECTORS.read_text(encoding="utf-8"))
    h = CarHealth(data["model"], data["powertrain"])
    for op in data["ops"]:
        if op["op"] == "trip":
            for rep in op["reports"]:
                for q in config.METRICS:
                    if q in rep:
                        v = h.trackers[q].report_value(rep[q]["y"])
                        assert (v is None) == (rep[q]["v"] is None)
                        if v is not None:
                            assert v == pytest.approx(rep[q]["v"], abs=1e-12)
            for q in config.METRICS:
                h.trackers[q].observe_trip(op["trip"], [tuple(x) for x in op["items"][q]])
        else:
            h.trackers[op["metric"]].refit(op["rs"], op["day"])
        exp = op["expect"]
        for q in config.METRICS:
            t = h.trackers[q]
            for key in ("learned", "baseline", "sigma", "tau", "s_pos", "s_neg", "alarm_shift", "calm"):
                got, want = getattr(t, key), exp[q][key]
                assert (got is None) == (want is None), (op, q, key)
                if want is not None:
                    assert got == pytest.approx(want, abs=1e-12), (op, q, key)
        f = h.finding()
        assert (f.kind if f else None) == exp["finding"]
        assert h.speech() == exp["speech"]
