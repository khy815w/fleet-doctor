"""시뮬레이터의 '정답 세계': 길의 실제 몫, 날씨, 차종, 차, 고장.

서버와 차의 판단 로직은 이 정답을 모른다. 차가 재는 값 y만 보고 추정한다.
숫자는 실제 차를 잰 값이 아니라, 그럴듯한 크기로 정한 가정이다(README '시뮬레이션 가정' 참고).
"""
from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from fleetdoc import config  # noqa: E402
from fleetdoc.world import World  # noqa: E402

WORLD_FILE = Path(__file__).resolve().parent.parent / "server" / "data" / "world_demo.json"


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    powertrain: str        # ev · ice
    k_steer: float         # 같은 길 기울기·옆바람에 핸들을 돌리는 양 (기준 차 = 1)
    k_road: float          # 같은 오르막·맞바람에 에너지가 느는 비율 (기준 차 = 1)
    temp_per_c: float      # 18°C보다 1°C 추울 때마다 에너지가 느는 비율(로그)
    speed_slope: float     # 1km/h 빠를 때마다 에너지가 느는 비율(로그). 차는 0.0095로 보정한다
    base: float            # 1km당 에너지 (전기차 kWh, 내연기관 L)


MODELS = [
    Model("A", "전기 세단", "ev", 0.85, 0.90, 0.010, 0.0090, 0.140),
    Model("B", "전기 SUV", "ev", 1.10, 1.15, 0.013, 0.0105, 0.175),
    Model("C", "소형 전기 SUV", "ev", 1.00, 1.00, 0.012, 0.0095, 0.150),
    Model("D", "대형 전기 SUV", "ev", 1.30, 1.20, 0.015, 0.0110, 0.205),
    Model("E", "가솔린 세단", "ice", 0.90, 0.85, 0.003, 0.0085, 0.075),
    Model("F", "하이브리드 SUV", "ice", 1.15, 1.05, 0.004, 0.0090, 0.060),
]
MODEL_WEIGHTS = [0.2, 0.2, 0.2, 0.1, 0.15, 0.15]
MODEL_BY_ID = {m.id: m for m in MODELS}

FAULT_KINDS = {"alignment": "pull", "slow_leak": "tire", "brake_drag": "drag"}
# 알맞은 안내: 쏠림 안내(pull·tire)는 둘 다 '타이어 공기압 → 바퀴 정렬' 순서로 점검하라고 한다
RIGHT_ADVICE = {"alignment": {"pull", "tire"}, "slow_leak": {"pull", "tire"}, "brake_drag": {"drag"}}


@dataclass
class Weather:
    temp_c: float
    wind_ms: float
    wind_to: float         # 바람이 불어 가는 방위(도)


class Truth:
    """길과 날씨의 실제 몫."""

    def __init__(self, world: World, seed: int, day0: int, days: int):
        rng = random.Random(seed)
        self.world = world
        self.day0, self.days = day0, days
        self.crown = {}          # 구간 → 핸들 몫(기준 차, 도). 길이 바깥쪽으로 기울어 왼쪽으로 조금 돌린다
        self.grade = {}          # 구간 → 에너지 몫(기준 차, 로그). 오르막 +, 내리막 −
        links: dict[str, float] = {}
        for s in world.segments:
            self.crown[s.id] = rng.gauss(-0.35, 0.30)
            link = s.id[:-1]
            if link not in links:
                links[link] = 0.0 if rng.random() < 0.3 else rng.gauss(0.0, 0.045)
            forward = s.id[-1] in "NE"
            self.grade[s.id] = links[link] if forward else -links[link]
        self.weather = {}
        for k in range(days):
            day = day0 + k
            temp = 15.0 - 0.27 * k + rng.gauss(0.0, 2.5)                # 10월 → 12월, 점점 추워진다
            wind = 1.5 + rng.expovariate(1 / 1.5)
            if rng.random() < 0.12:                                       # 바람 센 날
                wind += rng.uniform(6.0, 10.0)
            self.weather[day] = Weather(temp, wind, rng.uniform(0.0, 360.0))

    def steer(self, seg_id: str, model: Model, day: int) -> float:
        s = self.world.by_id[seg_id]
        w = self.weather[day]
        cross = w.wind_ms * math.sin(math.radians(w.wind_to - s.heading))   # +: 오른쪽으로 미는 바람
        return model.k_steer * (self.crown[seg_id] - 0.075 * cross)

    def eff(self, seg_id: str, model: Model, day: int) -> float:
        s = self.world.by_id[seg_id]
        w = self.weather[day]
        head = -w.wind_ms * math.cos(math.radians(w.wind_to - s.heading))   # +: 맞바람
        g = self.grade[seg_id]
        if g < 0:                                                           # 내리막에서 되찾는 에너지는 일부뿐
            g *= 0.65 if model.powertrain == "ev" else 0.45
        temp = model.temp_per_c * max(0.0, 18.0 - w.temp_c)
        return model.k_road * (g + 0.010 * head) + temp


@dataclass
class Fault:
    kind: str              # alignment · slow_leak · brake_drag
    start: int             # 시작 날짜
    steer: float           # 다 진행됐을 때 핸들 변화(도)
    eff: float             # 다 진행됐을 때 에너지 변화(로그)
    ramp_days: int = 0     # 0이면 한 번에, 아니면 이 일수 동안 서서히 (바람 빠짐)

    def at(self, day: int) -> tuple[float, float]:
        if day < self.start:
            return 0.0, 0.0
        frac = 1.0 if self.ramp_days <= 0 else min(1.0, (day - self.start + 1) / self.ramp_days)
        return self.steer * frac, self.eff * frac

    @property
    def tpms_day(self) -> int | None:
        """타이어 공기압 경고등이 켜지는 날(공기압 25% 빠짐 = 램프 끝). 바람 빠짐에만 있다."""
        return self.start + self.ramp_days if self.kind == "slow_leak" else None


@dataclass
class Car:
    idx: int
    model: Model
    offset: float          # 핸들 센서 0점 오차 + 원래 정렬 상태(도)
    style: float           # 운전 습관(에너지, 로그)
    style2: float | None   # 다른 가족이 몰 때 습관. 없으면 혼자 탄다
    home: tuple[int, int]
    work: tuple[int, int]
    fault: Fault | None = None
    nuisance: Fault | None = None     # 고장은 아니지만 생기는 작은 변화(타이어 위치 교환 등)
    seed: int = 0
    rng: random.Random = field(default_factory=random.Random, repr=False)


@dataclass
class Pass:
    seg: str
    steer: float | None
    eff: float | None
    trip: int = 0          # 같은 차의 주행 번호 (날짜 × 10 + 그날 몇 번째)


class Fleet:
    def __init__(self, world: World, truth: Truth, n_cars: int, seed: int, fault_share: float = 0.10):
        rng = random.Random(seed + 1)
        self.world, self.truth = world, truth
        self.cars: list[Car] = []
        n = max(int(s.road[1:]) for s in world.segments)          # 바둑판 한 변의 교차로 수(5)
        self.n_nodes = n
        for i in range(n_cars):
            model = rng.choices(MODELS, MODEL_WEIGHTS)[0]
            home = (rng.randrange(n), rng.randrange(n))
            while True:
                work = (rng.randrange(n), rng.randrange(n))
                if abs(work[0] - home[0]) + abs(work[1] - home[1]) >= 2:
                    break
            car = Car(i, model, rng.gauss(0.0, 0.8), rng.gauss(0.0, 0.05),
                      rng.gauss(0.0, 0.07) if rng.random() < 0.3 else None, home, work,
                      seed=seed * 100_003 + i)
            car.rng = random.Random(car.seed)
            if rng.random() < fault_share:
                car.fault = self._random_fault(rng, truth.day0)
            elif rng.random() < 0.06:
                start = truth.day0 + rng.randint(20, truth.days - 5)
                car.nuisance = Fault("rotation", start, rng.choice((-1, 1)) * rng.uniform(0.1, 0.4),
                                     rng.uniform(-0.008, 0.008))
            self.cars.append(car)

    @staticmethod
    def _random_fault(rng: random.Random, day0: int) -> Fault:
        start = day0 + rng.randint(24, 46)
        side = rng.choice((-1, 1))
        kind = rng.choices(list(FAULT_KINDS), [0.4, 0.3, 0.3])[0]
        if kind == "alignment":          # 포트홀 충격: 정렬이 한 번에 틀어진다
            return Fault(kind, start, side * rng.uniform(1.2, 3.0), rng.uniform(0.005, 0.015))
        if kind == "slow_leak":          # 한쪽 타이어 바람이 1~2주에 걸쳐 빠진다
            return Fault(kind, start, side * rng.uniform(0.8, 1.6), rng.uniform(0.02, 0.045),
                         ramp_days=rng.randint(6, 14))
        return Fault(kind, start, side * rng.uniform(0.2, 0.6), rng.uniform(0.05, 0.10))   # 브레이크 끌림

    # ── 하루 주행 ───────────────────────────────────────────────
    def path(self, a: tuple[int, int], b: tuple[int, int], x_first: bool) -> list[str]:
        """바둑판 위 a → b. 가로 먼저 또는 세로 먼저."""
        segs = []
        (x, y), (tx, ty) = a, b

        def go_x():
            nonlocal x
            while x != tx:
                if tx > x:
                    segs.append(f"H{y + 1}-{x + 1}E")
                    x += 1
                else:
                    segs.append(f"H{y + 1}-{x}W")
                    x -= 1

        def go_y():
            nonlocal y
            while y != ty:
                if ty > y:
                    segs.append(f"V{x + 1}-{y + 1}N")
                    y += 1
                else:
                    segs.append(f"V{x + 1}-{y}S")
                    y -= 1

        if x_first:
            go_x(); go_y()
        else:
            go_y(); go_x()
        return segs

    def trips(self, car: Car, day: int) -> list[list[str]]:
        r = car.rng
        weekend = (day + 3) % 7 in (5, 6)          # 2026-10-01(day 0)은 목요일
        if not weekend:
            if r.random() > 0.92:
                return []
            return [self.path(car.home, car.work, r.random() < 0.5),
                    self.path(car.work, car.home, r.random() < 0.5)]
        if r.random() > 0.6:
            return []
        dest = (r.randrange(self.n_nodes), r.randrange(self.n_nodes))
        return [self.path(car.home, dest, r.random() < 0.5), self.path(dest, car.home, r.random() < 0.5)]

    def drive(self, car: Car, day: int) -> list[Pass]:
        """그날 지난 구간마다 차가 잰 값. 일정한 속도가 아니면 값이 없다."""
        r, m, t = car.rng, car.model, self.truth
        out = []
        f_steer, f_eff = car.fault.at(day) if car.fault else (0.0, 0.0)
        n_steer, n_eff = car.nuisance.at(day) if car.nuisance else (0.0, 0.0)
        for ti, trip in enumerate(self.trips(car, day)):
            trip_id = day * 10 + ti
            style = car.style2 if (car.style2 is not None and r.random() < 0.25) else car.style
            for seg_id in trip:
                seg = self.world.by_id[seg_id]
                if r.random() > 0.78:                         # 신호·정체로 일정한 속도가 아니었다
                    out.append(Pass(seg_id, None, None, trip_id))
                    continue
                v = seg.v_ref_kmh + r.gauss(0.0, 4.0)
                steer = None
                if seg.straight:
                    steer = car.offset + t.steer(seg_id, m, day) + f_steer + n_steer + r.gauss(0.0, 0.40)
                    if r.random() < 0.06:                     # 차로 변경이 섞였다
                        steer += r.gauss(0.0, 1.5)
                eff = (math.log(m.base) + style + t.eff(seg_id, m, day) + f_eff + n_eff
                       + (m.speed_slope - config.SPEED_SLOPE_PER_KMH) * (v - seg.v_ref_kmh)
                       + r.gauss(0.0, 0.035))
                if r.random() < 0.10:                         # 에어컨·히터를 세게 틀었다
                    eff += r.uniform(0.03, 0.12)
                out.append(Pass(seg_id, steer, eff, trip_id))
        return out


def load_world() -> World:
    return World.load(WORLD_FILE)


# ── 에뮬레이터 시연용 차 한 대 ─────────────────────────────────────────
DEMO_MODEL = "C"
DEMO_LOOP = ["V2-1N", "V2-2N", "V2-3N", "H4-2E", "H4-3E", "V4-3S", "V4-2S", "V4-1S", "H1-3W", "H1-2W"]


def demo_car(seed: int = 11) -> Car:
    """에뮬레이터 차. 시뮬레이터 차들과 같은 세계에서 같은 규칙으로 달린다."""
    car = Car(-1, MODEL_BY_ID[DEMO_MODEL], offset=0.6, style=0.0, style2=None, home=(1, 0), work=(3, 3),
              seed=seed)
    car.rng = random.Random(seed)
    return car
