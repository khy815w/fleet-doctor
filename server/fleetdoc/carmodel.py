"""차 안에서 도는 판단의 기준 구현. 앱의 health/CarHealth.kt가 같은 계산을 한다.
두 구현은 tests/vectors/carhealth.json 하나로 함께 시험한다.

구간을 한 번 지날 때마다(pass) 지표별 값 y가 하나 나온다.
- steer: 똑바른 구간을 일정한 속도로 지날 때 핸들 각도 평균(도). 핸들을 오른쪽으로 돌린 쪽이 +
         (Pleos SDK는 앞바퀴 각도를 주므로 앱이 기준 조향비 15를 곱해 핸들 각도로 바꾼다)
- eff:   그 구간에서 1km당 쓴 에너지(kWh 또는 L)의 자연로그(속도 보정). 0.04 차이 ≈ 4%

서버가 주는 길 몫 r(구간, 차종, 날짜)을 빼면 z = y − r 에는 '내 차 몫'만 남는다.
1) 배우기: 처음 30번(7일, 10번 주행 이상)의 z로 평소 값 a와 흔들림을 잡는다. 차종 차이는 a에 들어간다.
   흔들림은 둘로 나눠 잡는다. σ: 같은 주행 안에서 구간마다 흔들리는 정도, τ: 주행마다 달라지는 정도
   (다른 가족이 몰았다, 짐을 실었다, 히터를 세게 틀었다처럼 한 번 주행 내내 이어지는 것).
2) 지켜보기: 주행이 끝날 때마다 그 주행의 평균 편차 m을 CUSUM(로그 우도비)으로 쌓는다.
   누적 점수가 문턱을 넘고, 최근 4번 주행(구간 6개 이상)의 평균 편차도 기준의 70%를 넘고,
   그중 2번 이상이 같은 쪽으로 벗어났을 때만 알린다(한 번 튄 주행으로는 알리지 않는다).
3) 보고: 의심스럽지 않을 때만 구간마다 v = y − a 를 차 ID 없이 서버에 보낸다. 서버는 이것으로 길 몫을 잡는다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import config

STEER, EFF = "steer", "eff"


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


@dataclass
class Sample:
    segment: str
    day: int
    trip: int
    y: float


class MetricTracker:
    """지표 하나(steer 또는 eff)의 평소 값과 변화 감지."""

    def __init__(self, metric: str):
        self.metric = metric
        self.target = config.TARGET[metric]
        self.h = config.CUSUM_H[metric]
        self.two_sided = metric == STEER      # 에너지는 '더 쓸 때'만 고장 신호다
        self.learn: list[Sample] = []
        self.baseline: float | None = None
        self.sigma: float | None = None       # 주행 안 구간마다의 흔들림
        self.tau: float | None = None         # 주행마다의 흔들림
        self.learned = False
        self.fit_day: int | None = None
        self.s_pos = 0.0
        self.s_neg = 0.0
        self.recent: list[tuple[float, int, float]] = []   # 최근 주행의 (판단용 평균 편차, 구간 수, 크기용 평균 편차)
        self.alarm_shift: float | None = None
        self.calm = 0

    # ── 배우기 ──────────────────────────────────────────────────
    def learn_days(self) -> int:
        return len({s.day for s in self.learn})

    def learn_trips(self) -> int:
        return len({s.trip for s in self.learn})

    def learn_keys(self) -> list[tuple[str, int]]:
        """다시 계산할 때 서버에 길 몫을 물어볼 (구간, 날짜) 목록."""
        return [(s.segment, s.day) for s in self.learn]

    def needs_refit(self, day: int) -> bool:
        if not self.learned:
            return len(self.learn) >= config.REPORT_MIN_PASSES and self.fit_day != day
        return self.fit_day is None or day - self.fit_day >= config.RECOMPUTE_EVERY_DAYS

    def refit(self, rs: list[float], day: int) -> None:
        """배운 통과들의 y와, 그 통과들의 최신 길 몫 rs로 평소 값과 흔들림을 다시 잡는다."""
        if len(rs) != len(self.learn) or not rs:
            raise ValueError("rs must match learning samples")
        floor_s, floor_t = config.SIGMA_FLOOR[self.metric], config.TAU_FLOOR[self.metric]
        z = [s.y - r for s, r in zip(self.learn, rs)]
        med = _median(z)
        s0 = max(floor_s, 1.4826 * _median([abs(v - med) for v in z]))
        lim = config.CLIP_SIGMA * s0
        zw = [_clip(v, med - lim, med + lim) for v in z]      # 차로 변경처럼 크게 튄 값은 자른다
        base = sum(zw) / len(zw)

        trips: dict[int, list[float]] = {}
        for s, v in zip(self.learn, zw):
            trips.setdefault(s.trip, []).append(v)
        ss, dof = 0.0, 0
        for vals in trips.values():
            if len(vals) >= 2:
                mu = sum(vals) / len(vals)
                ss += sum((v - mu) ** 2 for v in vals)
                dof += len(vals) - 1
        sigma = math.sqrt(ss / dof) if dof >= 5 else s0
        sigma = max(floor_s, sigma)
        between, noise = 0.0, 0.0
        for vals in trips.values():
            mu = sum(vals) / len(vals)
            between += (mu - base) ** 2
            noise += sigma * sigma / len(vals)
        tau2 = (between - noise) / len(trips)
        self.baseline, self.sigma, self.tau = base, sigma, max(floor_t, math.sqrt(max(0.0, tau2)))
        self.fit_day = day
        if not self.learned and len(self.learn) >= config.LEARN_PASSES \
                and self.learn_days() >= config.LEARN_MIN_DAYS and self.learn_trips() >= config.LEARN_MIN_TRIPS:
            self.learned = True

    # ── 보고 ────────────────────────────────────────────────────
    def report_value(self, y: float) -> float | None:
        """서버에 보낼 익명 편차 v = y − a. 아직 모르거나 내 차가 의심스러우면 보내지 않는다."""
        if self.baseline is None or self.alarm_shift is not None:
            return None
        if self.s_pos > self.h / 2 or self.s_neg > self.h / 2:
            return None
        return y - self.baseline

    # ── 지켜보기 ────────────────────────────────────────────────
    def shift(self) -> float:
        """최근 주행들의 평균 편차(구간 수 가중). 판단에 쓴다."""
        n = sum(k for _, k, _ in self.recent)
        return sum(m * k for m, k, _ in self.recent) / n if n else 0.0

    def estimate(self) -> float:
        """알림 문장에 쓸 변화량: 최근 주행 중 같은 쪽으로 벗어난 주행들만, 덜 자른 값으로 평균한다."""
        sign = 1.0 if self.shift() >= 0 else -1.0
        need = config.MIN_SHIFT_RATIO * self.target
        rows = [(raw, k) for m, k, raw in self.recent if sign * m >= need / 2]
        n = sum(k for _, k in rows)
        return sum(raw * k for raw, k in rows) / n if n else self.shift()

    def _confirmed(self, sign: int) -> bool:
        """최근 주행 평균이 기준의 70%를 넘고, 그중 둘 이상이 같은 쪽으로 기준의 절반 넘게 벗어났나.
        한두 번 튄 주행(차로 변경, 다른 가족이 몬 날)만으로는 알리지 않는다."""
        need = config.MIN_SHIFT_RATIO * self.target
        if sum(k for _, k, _ in self.recent) < config.MIN_WINDOW_PASSES:
            return False
        agree = sum(1 for m, _, _ in self.recent if sign * m >= need / 2)
        return sign * self.shift() >= need and agree >= config.AGREE_TRIPS

    def observe_trip(self, trip: int, items: list[tuple[str, int, float, float]]) -> None:
        """주행 한 번이 끝났다. items = [(구간, 날짜, y, r), ...]"""
        if not items:
            return
        if not self.learned:
            self.learn += [Sample(seg, day, trip, y) for seg, day, y, _ in items]
            return
        raw = [(y - r) - self.baseline for _, _, y, r in items]
        lim = config.CLIP_SIGMA * self.sigma
        n = len(raw)
        # 판단용: 평소 값에서 4σ 넘게 튄 구간(차로 변경)은 자른다
        m = sum(_clip(v, -lim, lim) for v in raw) / n
        # 크기용: 큰 고장도 제 크기로 말하도록, 그 주행의 가운데 값 기준으로만 자른다
        if n >= 3:
            med = _median(raw)
            raw = [_clip(v, med - lim, med + lim) for v in raw]
        m_raw = sum(raw) / n
        var = self.tau * self.tau + self.sigma * self.sigma / n
        d = self.target
        cap = config.CUSUM_CAP_RATIO * self.h
        self.s_pos = min(cap, max(0.0, self.s_pos + d / var * (m - d / 2)))
        self.s_neg = min(cap, max(0.0, self.s_neg + d / var * (-m - d / 2))) if self.two_sided else 0.0
        self.recent.append((m, n, m_raw))
        if len(self.recent) > config.RECENT_TRIPS:
            self.recent.pop(0)
        if self.alarm_shift is None:
            if (self.s_pos > self.h and self._confirmed(+1)) or (self.s_neg > self.h and self._confirmed(-1)):
                self.alarm_shift = self.estimate()
            return
        self.calm = self.calm + 1 if abs(self.shift()) < config.CLEAR_RATIO * d else 0
        if self.calm >= config.CLEAR_TRIPS:       # 정비 뒤 원래대로 돌아왔다
            self.alarm_shift, self.calm, self.s_pos, self.s_neg = None, 0, 0.0, 0.0
        else:
            self.alarm_shift = self.estimate()

    def remaining(self) -> tuple[int, int, int]:
        """다 배우려면 남은 (통과 수, 날짜 수, 주행 수)."""
        if self.learned:
            return 0, 0, 0
        return (max(0, config.LEARN_PASSES - len(self.learn)),
                max(0, config.LEARN_MIN_DAYS - self.learn_days()),
                max(0, config.LEARN_MIN_TRIPS - self.learn_trips()))

    def reset(self) -> None:
        """정비했다고 알려 주면 평소 값을 처음부터 다시 배운다."""
        self.__init__(self.metric)

    def to_dict(self) -> dict:
        return {"metric": self.metric, "learned": self.learned, "baseline": self.baseline,
                "sigma": self.sigma, "tau": self.tau, "fit_day": self.fit_day,
                "s_pos": self.s_pos, "s_neg": self.s_neg, "recent": [list(x) for x in self.recent],
                "alarm_shift": self.alarm_shift, "calm": self.calm,
                "learn": [[s.segment, s.day, s.trip, s.y] for s in self.learn]}


@dataclass
class Finding:
    kind: str          # pull(핸들 쏠림) · tire(쏠림 + 에너지도 더 씀: 타이어 공기압 의심) · drag(에너지만 더 씀)
    steer_shift: float
    eff_shift: float


class CarHealth:
    """차 한 대. 지표 둘을 지켜보고, 알릴 것이 있으면 무엇인지 고른다."""

    TIRE_EFF = 0.02   # 쏠림과 함께 에너지도 이만큼(약 2%) 넘게 꾸준히 더 쓰면 타이어 공기압을 먼저 의심한다

    def __init__(self, model: str, powertrain: str = "ev"):
        self.model = model
        self.powertrain = powertrain
        self.trackers = {STEER: MetricTracker(STEER), EFF: MetricTracker(EFF)}

    def finding(self) -> Finding | None:
        st, ef = self.trackers[STEER], self.trackers[EFF]
        if st.alarm_shift is None and ef.alarm_shift is None:
            return None
        steer_now = st.estimate() if st.alarm_shift is not None else (st.shift() if st.learned else 0.0)
        eff_now = ef.estimate() if ef.alarm_shift is not None else (ef.shift() if ef.learned else 0.0)
        if st.alarm_shift is not None:
            eff_rising = ef.alarm_shift is not None or (ef.learned and ef.shift() >= self.TIRE_EFF)
            kind = "tire" if eff_rising else "pull"
        else:
            kind = "drag"
        return Finding(kind, steer_now, eff_now)

    def speech(self) -> str:
        """'내 차 괜찮아?'에 대한 답이자, 알림 문장."""
        f = self.finding()
        if f is not None:
            return describe(f, self.powertrain)
        passes = max(t.remaining()[0] for t in self.trackers.values())
        days = max(t.remaining()[1] for t in self.trackers.values())
        trips = max(t.remaining()[2] for t in self.trackers.values())
        if passes:
            return f"아직 평소 상태를 배우는 중이에요. 똑바른 길을 {passes}번 더 지나면 비교할 수 있어요."
        if days:
            return f"아직 평소 상태를 배우는 중이에요. {days}일 더 타면 비교할 수 있어요."
        if trips:
            return f"아직 평소 상태를 배우는 중이에요. {trips}번 더 타면 비교할 수 있어요."
        energy = "전기" if self.powertrain == "ev" else "기름"
        return f"핸들 쏠림도, {energy} 쓰는 양도 평소와 같아요."


def _deg(x: float) -> str:
    x = abs(x)
    return f"{x:.0f}" if x >= 1.5 else f"{x:.1f}"


def describe(f: Finding, powertrain: str) -> str:
    energy_obj = "전기를" if powertrain == "ev" else "기름을"
    energy_also = "전기도" if powertrain == "ev" else "기름도"
    pct = max(1, round((math.exp(f.eff_shift) - 1.0) * 100))
    wheel = "오른" if f.steer_shift > 0 else "왼"     # 똑바로 가려고 핸들을 더 돌린 쪽
    pull = "왼" if f.steer_shift > 0 else "오른"      # 차가 쏠리는 쪽
    if f.kind == "pull":
        return (f"차가 {pull}쪽으로 쏠리고 있어요. 똑바로 가려면 핸들을 평소보다 {wheel}쪽으로 "
                f"약 {_deg(f.steer_shift)}도 더 돌려야 해요. {pull}쪽 타이어 공기압을 먼저 보고, "
                f"괜찮으면 바퀴 정렬 점검을 권해요.")
    if f.kind == "tire":
        return (f"차가 {pull}쪽으로 쏠리고, {energy_also} 평소보다 약 {pct}퍼센트 더 써요. "
                f"{pull}쪽 타이어 공기압부터 확인해 보세요. 공기압이 괜찮으면 바퀴 정렬을 점검하세요.")
    return (f"길과 날씨 영향을 빼고 보면, {energy_obj} 평소보다 약 {pct}퍼센트 더 쓰고 있어요. "
            f"브레이크 끌림이나 타이어 공기압 점검을 권해요.")


def adjust_eff(per_km: float, speed_kmh: float, v_ref_kmh: float) -> float:
    """1km당 에너지를 로그로 바꾸고, 기준 속도로 보정한다."""
    return math.log(per_km) - config.SPEED_SLOPE_PER_KMH * (speed_kmh - v_ref_kmh)
