"""플릿 닥터 서버 (FastAPI). 메시지 형식은 docs/protocol.md.

서버가 아는 것은 '어느 구간에서, 어느 차종이, 며칠에, 평소보다 얼마나 달랐나'뿐이다.
차 ID·주행 기록·차마다의 평소 값은 차 안에만 있다. 그래서 서버는 어느 차가 고장인지 모른다.

POST /v1/observations   익명 편차 보고
GET  /v1/effects        오늘의 길 몫
POST /v1/effects/query  지난 날짜의 길 몫 (평소 값 다시 계산용)
GET  /v1/world          구간 목록
GET  /v1/models         차종 묶음
GET  /v1/state          관제 화면 요약
POST/GET /v1/demo/*     시연 전용 (FLEETDOC_DEMO=1): 시연 차 기록, 에뮬레이터 차량 신호 통로
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .effects import EffectStore
from .world import World, day_of

SERVER_DIR = Path(__file__).resolve().parent.parent
ROOT = SERVER_DIR.parent
WORLD_FILE = Path(os.getenv("FLEETDOC_WORLD_FILE", SERVER_DIR / "data" / "world_demo.json"))
MODELS_FILE = SERVER_DIR / "data" / "models.json"
COMPARE_FILE = ROOT / "simulator" / "results" / "compare.json"
DEMO = os.getenv("FLEETDOC_DEMO", "1") == "1"
MAX_PAST_DAYS = 60
MAX_ITEMS = 5000
FEED_KEEP = 20000          # 시연 신호 통로에 남겨 두는 표본 수


class ObservationItem(BaseModel):
    segment: str = Field(min_length=1, max_length=32)
    steer: float | None = Field(default=None, ge=-30, le=30)
    eff: float | None = Field(default=None, ge=-2, le=2)


class ObservationsIn(BaseModel):
    model: str = Field(min_length=1, max_length=8)
    day: int
    items: list[ObservationItem] = Field(max_length=MAX_ITEMS)


class QueryItem(BaseModel):
    segment: str = Field(min_length=1, max_length=32)
    day: int


class QueryIn(BaseModel):
    model: str = Field(min_length=1, max_length=8)
    items: list[QueryItem] = Field(max_length=500)


class DemoPass(BaseModel):
    segment: str
    day: int
    trip: int
    steer: float | None = None
    eff: float | None = None


class DemoHistoryIn(BaseModel):
    model: str
    powertrain: str = "ev"
    passes: list[DemoPass] = Field(max_length=20000)


class FeedSample(BaseModel):
    """에뮬레이터 차의 차량 신호 한 순간. t는 시연 시계(초)라서 실제 시각보다 빨리 갈 수 있다."""
    t: float
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    heading: float
    speed_kmh: float = Field(ge=0, le=300)
    wheel_deg: float | None = None       # 앞바퀴 각도(도, 오른쪽 +). Pleos SDK가 주는 값과 같은 꼴
    consumption: float | None = None     # 지금 1km당 에너지(kWh/km 또는 L/km)


class FeedIn(BaseModel):
    samples: list[FeedSample] = Field(max_length=5000)


def _r(x: float, nd: int = 5) -> float:
    return round(float(x), nd) if math.isfinite(x) else 0.0


def create_app(clock=time.time) -> FastAPI:
    world = World.load(WORLD_FILE)
    models = json.loads(MODELS_FILE.read_text(encoding="utf-8"))
    model_ids = [m["id"] for m in models]
    store = EffectStore(world, model_ids)
    lock = threading.Lock()
    demo: dict = {"history": None, "feed": [], "seq": 0}

    def server_day() -> int:
        return day_of(clock())

    def fitted() -> EffectStore:
        with lock:
            if store.dirty or not store.fits:
                store.fit(server_day())
        return store

    def check_model(model: str) -> None:
        if model not in store.midx:
            raise HTTPException(400, f"unknown model {model!r}")

    app = FastAPI(title="Fleet Doctor")
    app.state.store = store

    @app.get("/v1/models")
    def get_models():
        return models

    @app.get("/v1/world")
    def get_world(cells: str | None = None):
        d = world.to_dict()
        if cells:
            wanted = {c for c in cells.split(",") if c}
            d["segments"] = [s for s in d["segments"] if s["cell"] in wanted]
        return d

    @app.post("/v1/observations")
    def post_observations(body: ObservationsIn):
        check_model(body.model)
        today = server_day()
        if not (today - MAX_PAST_DAYS <= body.day <= today + 1):
            raise HTTPException(400, "day out of range")
        with lock:
            n = store.add_items(body.model, body.day, [it.model_dump() for it in body.items])
        return {"accepted": n}

    @app.get("/v1/effects")
    def get_effects(model: str, day: int | None = None, segments: str = Query(default="")):
        check_model(model)
        today = server_day()
        d = today if day is None else day
        ids = [s for s in segments.split(",") if s][:200]
        st = fitted()
        with lock:
            eff = st.effects_for(model, d, ids)
        return {"model": model, "day": d, "server_day": today, "effects": eff}

    @app.post("/v1/effects/query")
    def query_effects(body: QueryIn):
        check_model(body.model)
        st = fitted()
        m = st.midx[body.model]
        rows = [(world.by_id.get(it.segment), it.day) for it in body.items]
        out = [{"steer": 0.0, "eff": 0.0} for _ in rows]
        ok = [i for i, (seg, _) in enumerate(rows) if seg is not None]
        if ok:
            idx = np.array([rows[i][0].idx for i in ok])
            days = np.array([rows[i][1] for i in ok])
            with lock:
                vals = {q: st.lookup(q, idx, m, days) for q in config.METRICS}
            for j, i in enumerate(ok):
                out[i] = {q: _r(vals[q][j]) for q in config.METRICS}
        return {"model": body.model, "values": out}

    @app.get("/v1/state")
    def get_state(model: str = "C"):
        check_model(model)
        st = fitted()
        m = st.midx[model]
        today = server_day()
        with lock:
            segs = []
            for s in world.segments:
                row = {"id": s.id, "straight": s.straight, "points": s.to_dict()["points"]}
                for q in config.METRICS:
                    f = st.fits.get(q)
                    row[q] = _r(f.B[s.idx, m]) if f is not None else 0.0
                    row[f"{q}_all"] = _r(f.B_all[s.idx]) if f is not None else 0.0
                    row[f"n_{q}"] = int(f.nB[s.idx, m]) if f is not None else 0
                segs.append(row)
            daily, scales = {}, {}
            for q in config.METRICS:
                f = st.fits.get(q)
                if f is None:
                    continue
                # 날짜별 몫: 방위 8칸을 보고 수로 가중 평균 (기간 평균 대비)
                w = f.nD + 1e-9                                    # (H, M, T)
                series = (f.D * w).sum(0) / w.sum(0)               # (M, T)
                daily[q] = {"day0": f.day0, "values": {mid: [_r(v, 4) for v in series[i]]
                                                       for i, mid in enumerate(model_ids)},
                            "counts": {mid: [int(c) for c in f.nD[:, i, :].sum(0)] for i, mid in enumerate(model_ids)}}
                scales[q] = {"static": {mid: _r(f.k_B[i], 3) for i, mid in enumerate(model_ids)},
                             "daily": {mid: _r(f.k_D[i], 3) for i, mid in enumerate(model_ids)}}
            reports = {q: int(len(st._data[q])) for q in config.METRICS}
        return {"server_day": today, "model": model, "models": models, "reports": reports,
                "total_reports": st.total_reports, "segments": segs, "daily": daily, "scales": scales}

    @app.get("/healthz")
    def health():
        return {"ok": True, "server_day": server_day(), "reports": store.total_reports}

    if DEMO:
        @app.post("/v1/demo/history")
        def post_history(body: DemoHistoryIn):
            check_model(body.model)
            demo["history"] = body.model_dump()
            return {"passes": len(body.passes)}

        @app.get("/v1/demo/history")
        def get_history():
            if demo["history"] is None:
                raise HTTPException(404, "no demo history")
            return demo["history"]

        @app.post("/v1/demo/feed")
        def post_feed(body: FeedIn):
            """drive_emulator.py가 시연 차의 차량 신호를 쌓는다. 앱(mock 빌드)이 이것을 차량 신호처럼 받는다."""
            with lock:
                for smp in body.samples:
                    demo["seq"] += 1
                    demo["feed"].append({"seq": demo["seq"], **smp.model_dump()})
                del demo["feed"][:-FEED_KEEP]
                return {"last": demo["seq"]}

        @app.get("/v1/demo/feed")
        def get_feed(after: int = -1, limit: int = Query(default=500, ge=1, le=2000)):
            """after보다 뒤의 표본. after<0이면 표본 없이 마지막 번호만 준다(앱을 켜기 전 신호는 건너뛴다)."""
            with lock:
                last = demo["seq"]
                if after < 0:
                    return {"last": last, "samples": []}
                return {"last": last, "samples": [x for x in demo["feed"] if x["seq"] > after][:limit]}

        @app.post("/v1/demo/clear")
        def clear():
            nonlocal store
            with lock:
                store = EffectStore(world, model_ids)
                app.state.store = store
            demo["history"] = None
            return {"ok": True}

        @app.get("/v1/demo/compare")
        def compare():
            if not COMPARE_FILE.exists():
                raise HTTPException(404, "run simulator/sim.py first")
            data = json.loads(COMPARE_FILE.read_text(encoding="utf-8"))
            for res in data["strategies"].values():
                res.pop("faults", None)
            return data

    static_dir = SERVER_DIR / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index():
        return FileResponse(static_dir / "index.html")

    return app


app = create_app()
