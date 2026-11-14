"""서버의 길 몫 추정: 차종마다 반응이 달라도 맞게 잡는가."""
import numpy as np
import pytest

from fleetdoc import config
from fleetdoc.effects import EffectStore

MODELS = ["A", "B", "C"]
SCALE = np.array([0.7, 1.0, 1.4])           # 같은 길에 차종마다 반응하는 비율


def synth(world, n_days=20, per_cell=6, seed=0):
    """구간마다 고정 몫 B, 날짜·방위마다 바뀌는 몫 D, 차종마다 비율이 다른 보고를 만든다."""
    rng = np.random.default_rng(seed)
    S = len(world.segments)
    B = rng.normal(0, 0.4, S)
    D = rng.normal(0, 0.3, (config.HEADING_BINS, n_days))
    hb = np.array([s.hbin for s in world.segments])
    rows = []
    for t in range(n_days):
        for m in range(len(MODELS)):
            for s in range(S):
                for _ in range(per_cell):
                    rows.append((s, m, t, SCALE[m] * (B[s] + D[hb[s], t]) + rng.normal(0, 0.4)))
    return np.array(rows), B, D, hb


def fill(store, rows):
    for m in range(len(MODELS)):
        for t in np.unique(rows[:, 2]):
            sel = (rows[:, 1] == m) & (rows[:, 2] == t)
            store.add("steer", rows[sel, 0], m, int(t), rows[sel, 3])


def truth_r(B, D, hb, s, m, t):
    return SCALE[m] * (B[s] + D[hb[s], t])


def centered_error(store, B, D, hb, m, t):
    """차는 평소 값으로 상수를 흡수하므로, 상수를 뺀 오차만 본다."""
    s = np.arange(len(B))
    est = store.lookup("steer", s, m, t)
    err = est - truth_r(B, D, hb, s, m, t)
    return np.std(err)


def test_model_aware_beats_model_blind(world):
    rows, B, D, hb = synth(world)
    aware, blind = EffectStore(world, MODELS), EffectStore(world, MODELS, model_aware=False)
    fill(aware, rows); fill(blind, rows)
    aware.fit(19); blind.fit(19)
    for m in (0, 2):                                         # 기준과 반응 비율이 다른 차종
        assert centered_error(aware, B, D, hb, m, 19) < 0.08
        assert centered_error(blind, B, D, hb, m, 19) > 2 * centered_error(aware, B, D, hb, m, 19)
    k = aware.fits["steer"].k_B
    assert k[2] / k[0] == pytest.approx(1.4 / 0.7, rel=0.1)  # 차종 환산 비율을 보고만으로 찾는다


def test_sparse_model_borrows_scaled_estimate(world):
    """C 차종 보고가 거의 없는 구간은 다른 차종 값을 비율로 환산해 쓴다."""
    rows, B, D, hb = synth(world, seed=1)
    keep = ~((rows[:, 1] == 2) & (rows[:, 0] >= 40))          # C는 구간 40번부터 보고가 없다
    store = EffectStore(world, MODELS)
    fill(store, rows[keep])
    store.fit(19)
    s = np.arange(40, len(B))
    err = store.lookup("steer", s, 2, 19) - truth_r(B, D, hb, s, 2, 19)
    assert np.std(err) < 0.15
    assert store.fits["steer"].nB[45, 2] == 0


def test_robust_to_a_few_bad_reports(world):
    rows, B, D, hb = synth(world, seed=2)
    bad = rows.copy()
    pick = np.random.default_rng(3).random(len(bad)) < 0.05
    bad[pick, 3] += 4.0                                        # 5%는 엉뚱하게 큰 값
    store = EffectStore(world, MODELS)
    fill(store, bad)
    store.fit(19)
    assert centered_error(store, B, D, hb, 1, 19) < 0.12


def test_unknown_day_uses_recent_then_zero(world):
    rows, B, D, hb = synth(world, n_days=5, seed=4)
    store = EffectStore(world, MODELS)
    fill(store, rows)
    store.fit(4)
    s = np.array([0, 1, 2])
    r4 = store.lookup("steer", s, 1, 4)
    assert np.allclose(store.lookup("steer", s, 1, 6), r4)    # 이틀 뒤까지는 마지막 날 몫을 그대로
    far = store.lookup("steer", s, 1, 4 + config.MAX_DAY_LAG + 1)
    assert np.allclose(far, store.fits["steer"].B[s, 1])       # 더 지나면 날짜 몫 없이 고정 몫만


def test_window_drops_old_reports(world):
    store = EffectStore(world, MODELS)
    store.add("eff", [0, 1], 0, 0, [0.1, 0.1])
    store.add("eff", [0, 1], 0, 50, [0.2, 0.2])
    store.fit(50)
    assert len(store._data["eff"]) == 2                        # 45일보다 오래된 보고는 버린다


def test_add_items_ignores_unknown(world):
    store = EffectStore(world, MODELS)
    n = store.add_items("A", 3, [{"segment": "V2-1N", "steer": 0.1, "eff": 0.02},
                                 {"segment": "nope", "steer": 0.3},
                                 {"segment": "V2-2N", "eff": float("nan")}])
    assert n == 2
    assert store.add_items("Z", 3, [{"segment": "V2-1N", "steer": 0.1}]) == 0
