import random
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

from fleetdoc.world import World  # noqa: E402


@pytest.fixture
def world() -> World:
    return World.load(SERVER_DIR / "data" / "world_demo.json")


def drive(health, days, trips_per_day=2, passes=4, steer=0.5, eff=-1.9, noise=(0.4, 0.035),
          steer_shift=None, eff_shift=None, seed=1, r=lambda q, seg, day: 0.0):
    """시험용 차 한 대를 몰고, 하루 끝마다 평소 값을 다시 계산한다.
    steer_shift/eff_shift: (시작 날짜, 크기) — 그날부터 생기는 고장."""
    rng = random.Random(seed)
    segs = [f"S{i}" for i in range(passes)]
    for day in days:
        for ti in range(trips_per_day):
            trip = day * 10 + ti
            for q, base, sd, shift in (("steer", steer, noise[0], steer_shift), ("eff", eff, noise[1], eff_shift)):
                extra = shift[1] if shift and day >= shift[0] else 0.0
                items = [(s, day, base + r(q, s, day) + extra + rng.gauss(0, sd), r(q, s, day)) for s in segs]
                health.trackers[q].observe_trip(trip, items)
        for q, t in health.trackers.items():
            if t.needs_refit(day):
                t.refit([r(q, s, d) for s, d in t.learn_keys()], day)
    return health
