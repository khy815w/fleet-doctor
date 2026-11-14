"""길 몫 추정 (서버).

보고 한 건은 (구간, 차종, 날짜, 지표, v)뿐이다. 차 ID도 정밀 위치도 없다.
v = y − a 는 그 차가 자기 평소 값 a를 뺀 값이라, 남는 것은 길 몫 + 잡음이다.

길 몫 = B[구간, 차종] + D[방위, 차종, 날짜]
- B: 그 구간에 늘 있는 몫. 길 기울기(핸들), 오르막·내리막(에너지)
- D: 날마다 바뀌는 몫. 바람(진행 방위마다 다르다), 기온(에너지)

차종마다 같은 길에서도 반응이 다르다(핸들을 돌리는 비율, 무게, 전기차·내연기관).
그래서 먼저 차종을 모두 합쳐 추정하고(B_all, D_all), 차종별로 다시 추정한 뒤
'차종 값 ≈ c + k × 전체 값' 환산식으로 보고가 적은 칸을 채운다. 보고가 n개인 칸은
같은 차종 값 비중 n/(n+10), 환산값 비중 10/(n+10).
"""
from __future__ import annotations

import numpy as np

from . import config
from .world import World


def _huber_mean(g: np.ndarray, r: np.ndarray, n: int, c: float, iters: int = 3):
    """묶음마다 크게 벗어난 값을 덜 믿는 평균(Huber)."""
    cnt = np.bincount(g, minlength=n).astype(float)
    mu = np.bincount(g, weights=r, minlength=n) / np.maximum(cnt, 1.0)
    for _ in range(iters):
        res = np.abs(r - mu[g])
        w = np.where(res <= c, 1.0, c / np.maximum(res, 1e-12))
        sw = np.bincount(g, weights=w, minlength=n)
        mu = np.bincount(g, weights=w * r, minlength=n) / np.maximum(sw, 1e-12)
    mu[cnt == 0] = 0.0
    return mu, cnt


def _backfit(s, ht, v, n_s, n_ht, group_of_ht, n_groups, c):
    """v ≈ B[s] + D[ht]를 번갈아 맞춘다. D는 묶음(방위, 차종)마다 기간 평균이 0이 되게 옮긴다."""
    D = np.zeros(n_ht)
    nD = np.zeros(n_ht)
    for _ in range(config.BACKFIT_ITERS):
        B, nB = _huber_mean(s, v - D[ht], n_s, c)
        D, nD = _huber_mean(ht, v - B[s], n_ht, c)
        tot = np.bincount(group_of_ht, weights=nD, minlength=n_groups)
        mean = np.bincount(group_of_ht, weights=D * nD, minlength=n_groups) / np.maximum(tot, 1.0)
        D = np.where(nD > 0, D - mean[group_of_ht], 0.0)
    B, nB = _huber_mean(s, v - D[ht], n_s, c)
    return B, nB, D, nD


def _scale_fit(raw: np.ndarray, n: np.ndarray, base: np.ndarray, n_base: np.ndarray):
    """차종마다 raw[:, m] ≈ c_m + k_m × base 를 보고 수 가중으로 맞춘다."""
    M = raw.shape[1]
    k = np.ones(M)
    c = np.zeros(M)
    for m in range(M):
        mask = (n[:, m] >= config.SCALE_MIN_N) & (n_base >= config.SCALE_MIN_N)
        if mask.sum() < 3:
            continue
        x, y, w = base[mask], raw[mask, m], n[mask, m]
        xm, ym = np.average(x, weights=w), np.average(y, weights=w)
        var = np.sum(w * (x - xm) ** 2)
        if var > 1e-9:
            k[m] = float(np.clip(np.sum(w * (x - xm) * (y - ym)) / var, *config.SCALE_LIMITS))
        c[m] = ym - k[m] * xm
    return k, c


class Fit:
    """지표 하나의 추정 결과."""

    def __init__(self, day0: int, T: int, B, nB, D, nD, B_all, nB_all, D_all, nD_all, k_B, k_D):
        self.day0, self.T = day0, T
        self.B, self.nB = B, nB                    # (S, M)
        self.D, self.nD = D, nD                    # (H, M, T)
        self.B_all, self.nB_all = B_all, nB_all    # (S,)
        self.D_all, self.nD_all = D_all, nD_all    # (H, T)
        self.k_B, self.k_D = k_B, k_D              # 차종별 환산 비율 (대시보드에 보여 준다)


class EffectStore:
    """익명 보고를 모으고 길 몫을 추정한다.

    model_aware=False면 차종을 가리지 않고 모두 같은 길 몫을 쓴다(비교 실험용).
    """

    def __init__(self, world: World, models: list[str], model_aware: bool = True):
        self.world = world
        self.models = list(models)
        self.midx = {m: i for i, m in enumerate(self.models)}
        self.model_aware = model_aware
        self.S, self.M, self.H = len(world.segments), len(self.models), config.HEADING_BINS
        self.seg_h = np.array([s.hbin for s in world.segments], dtype=np.int64)
        self._new: dict[str, list[np.ndarray]] = {q: [] for q in config.METRICS}
        self._data: dict[str, np.ndarray] = {q: np.zeros((0, 4)) for q in config.METRICS}
        self.fits: dict[str, Fit] = {}
        self.dirty = False
        self.total_reports = 0

    # ── 보고 받기 ───────────────────────────────────────────────
    def add(self, metric: str, seg_idx, model_idx, day, values) -> int:
        seg_idx = np.asarray(seg_idx, dtype=float).ravel()
        n = seg_idx.size
        if n == 0:
            return 0
        rows = np.empty((n, 4))
        rows[:, 0] = seg_idx
        rows[:, 1] = np.broadcast_to(np.asarray(model_idx, dtype=float), (n,))
        rows[:, 2] = np.broadcast_to(np.asarray(day, dtype=float), (n,))
        rows[:, 3] = np.asarray(values, dtype=float).ravel()
        self._new[metric].append(rows)
        self.dirty = True
        self.total_reports += n
        return n

    def add_items(self, model: str, day: int, items: list[dict]) -> int:
        """API 형식 [{segment, steer?, eff?}, ...]. 모르는 구간·차종은 버린다."""
        m = self.midx.get(model)
        if m is None:
            return 0
        accepted = 0
        for q in config.METRICS:
            segs, vals = [], []
            for it in items:
                seg = self.world.by_id.get(it.get("segment"))
                v = it.get(q)
                if seg is None or v is None or not np.isfinite(v):
                    continue
                segs.append(seg.idx)
                vals.append(float(v))
            accepted += self.add(q, segs, m, day, vals)
        return accepted

    # ── 추정 ────────────────────────────────────────────────────
    def fit(self, today: int) -> None:
        day_lo = today - config.EFFECT_WINDOW_DAYS + 1
        for q in config.METRICS:
            data = self._data[q]
            if self._new[q]:
                data = np.vstack([data, *self._new[q]])
                self._new[q] = []
            data = data[(data[:, 2] >= day_lo) & (data[:, 2] <= today)]
            self._data[q] = data
            if len(data) == 0:
                self.fits.pop(q, None)
                continue
            self.fits[q] = self._fit_metric(q, data, int(data[:, 2].min()), today)
        self.dirty = False

    def _fit_metric(self, q: str, data: np.ndarray, day0: int, today: int) -> Fit:
        S, M, H = self.S, self.M, self.H
        T = today - day0 + 1
        s = data[:, 0].astype(np.int64)
        m = data[:, 1].astype(np.int64)
        t = data[:, 2].astype(np.int64) - day0
        v = data[:, 3]
        h = self.seg_h[s]
        c = config.HUBER_C[q]

        # 1) 차종을 모두 합쳐
        ht_all = h * T + t
        B_all, nB_all, D_all, nD_all = _backfit(
            s, ht_all, v, S, H * T, np.repeat(np.arange(H), T), H, c)

        # 2) 차종별로
        sm = s * M + m
        hmt = (h * M + m) * T + t
        B_raw, nB, D_raw, nD = _backfit(
            sm, hmt, v, S * M, H * M * T, np.repeat(np.arange(H * M), T), H * M, c)
        B_raw, nB = B_raw.reshape(S, M), nB.reshape(S, M)
        D_raw = D_raw.reshape(H, M, T).transpose(0, 2, 1).reshape(H * T, M)   # (H·T, M)
        nD = nD.reshape(H, M, T).transpose(0, 2, 1).reshape(H * T, M)

        # 3) 환산식으로 빈 칸 채우기
        n0 = config.SHRINK_N0
        k_B, c_B = _scale_fit(B_raw, nB, B_all, nB_all)
        prior_B = c_B[None, :] + k_B[None, :] * B_all[:, None]
        B = (nB * B_raw + n0 * prior_B) / (nB + n0)
        k_D, c_D = _scale_fit(D_raw, nD, D_all, nD_all)
        prior_D = c_D[None, :] + k_D[None, :] * D_all[:, None]
        D = (nD * D_raw + n0 * prior_D) / (nD + n0)
        D = D.reshape(H, T, M).transpose(0, 2, 1)       # (H, M, T)
        nD = nD.reshape(H, T, M).transpose(0, 2, 1)
        return Fit(day0, T, B, nB, D, nD, B_all, nB_all, D_all.reshape(H, T), nD_all.reshape(H, T), k_B, k_D)

    # ── 조회 ────────────────────────────────────────────────────
    def _t_index(self, f: Fit, day) -> tuple[np.ndarray, np.ndarray]:
        t = np.asarray(day, dtype=np.int64) - f.day0
        last = f.T - 1
        ok = (t >= 0) & (t - last <= config.MAX_DAY_LAG)
        return np.clip(t, 0, last), ok

    def lookup(self, metric: str, seg_idx, model_idx, day) -> np.ndarray:
        """길 몫 r. 추정이 없으면 0."""
        seg_idx = np.asarray(seg_idx, dtype=np.int64)
        f = self.fits.get(metric)
        if f is None:
            return np.zeros(seg_idx.shape)
        model_idx = np.broadcast_to(np.asarray(model_idx, dtype=np.int64), seg_idx.shape)
        t, ok = self._t_index(f, np.broadcast_to(np.asarray(day), seg_idx.shape))
        h = self.seg_h[seg_idx]
        if self.model_aware:
            r = f.B[seg_idx, model_idx] + np.where(ok, f.D[h, model_idx, t], 0.0)
        else:
            r = f.B_all[seg_idx] + np.where(ok, f.D_all[h, t], 0.0)
        return r

    def effects_for(self, model: str, day: int, segments: list[str]) -> dict:
        """API 응답: {구간 ID: {steer, eff, n_steer, n_eff}}."""
        m = self.midx[model]
        segs = [self.world.by_id[s] for s in segments if s in self.world.by_id]
        idx = np.array([s.idx for s in segs], dtype=np.int64)
        out = {s.id: {} for s in segs}
        for q in config.METRICS:
            r = self.lookup(q, idx, m, day) if len(idx) else np.zeros(0)
            f = self.fits.get(q)
            for i, s in enumerate(segs):
                out[s.id][q] = round(float(r[i]), 5)
                out[s.id][f"n_{q}"] = int(f.nB[s.idx, m]) if f is not None else 0
        return out
