"""구간 맞추기, 날짜, 서버 API."""
import time

import pytest
from fastapi.testclient import TestClient

from fleetdoc import app as app_module
from fleetdoc import config
from fleetdoc.geo import LocalFrame
from fleetdoc.world import day_of, heading_bin


def test_day_epoch():
    assert day_of(config.EPOCH_KST_UNIX) == 0
    assert day_of(config.EPOCH_KST_UNIX + 31 * 86400 + 3600) == 31     # 2026-11-01 01:00 KST


def test_heading_bins():
    assert [heading_bin(h) for h in (0, 44, 46, 90, 180, 270, 359)] == [0, 1, 1, 2, 4, 6, 0]


def test_match_finds_segment_and_distance(world):
    seg = world.by_id["V2-1N"]
    (lat0, lon0), (lat1, lon1) = seg.points[0], seg.points[-1]
    lat, lon = lat0 + (lat1 - lat0) * 0.4, lon0 + (lon1 - lon0) * 0.4
    hit = world.match(lat, lon, 2.0)
    assert hit is not None and hit[0].id == "V2-1N"
    assert hit[1] == pytest.approx(0.4 * seg.length_m, abs=5)
    back = world.match(lat, lon, 181.0)
    assert back is not None and back[0].id == "V2-1S"             # 반대 방향은 다른 구간
    assert world.match(lat, lon, 90.0) is None                     # 옆으로 가고 있으면 아무 구간도 아니다


def test_match_rejects_far_points(world):
    seg = world.by_id["H1-2E"]
    f = LocalFrame(*seg.points[0])
    lat, lon = f.to_latlon(300.0, 40.0)                             # 길에서 40m 떨어진 곳
    assert world.match(lat, lon, 90.0) is None


@pytest.fixture
def client():
    return TestClient(app_module.create_app(clock=lambda: config.EPOCH_KST_UNIX + 31 * 86400 + 600))


def test_observations_and_effects_roundtrip(client):
    items = [{"segment": "V2-1N", "steer": 0.3, "eff": 0.05}] * 30 + [{"segment": "V2-2N", "steer": -0.2}] * 30
    r = client.post("/v1/observations", json={"model": "C", "day": 31, "items": items})
    assert r.status_code == 200 and r.json() == {"accepted": 90}
    e = client.get("/v1/effects", params={"model": "C", "segments": "V2-1N,V2-2N,nope"}).json()
    assert e["server_day"] == 31 and set(e["effects"]) == {"V2-1N", "V2-2N"}
    assert e["effects"]["V2-1N"]["steer"] > e["effects"]["V2-2N"]["steer"]
    q = client.post("/v1/effects/query", json={"model": "C", "items": [
        {"segment": "V2-1N", "day": 31}, {"segment": "nope", "day": 31}]}).json()
    assert q["values"][0]["steer"] == pytest.approx(e["effects"]["V2-1N"]["steer"], abs=1e-4)
    assert q["values"][1] == {"steer": 0.0, "eff": 0.0}


def test_rejects_unknown_model_and_bad_day(client):
    assert client.post("/v1/observations", json={"model": "Z", "day": 31, "items": []}).status_code == 400
    assert client.post("/v1/observations", json={"model": "C", "day": 31 - 61, "items": []}).status_code == 400
    assert client.post("/v1/observations", json={"model": "C", "day": 33, "items": []}).status_code == 400
    assert client.post("/v1/observations", json={"model": "C", "day": 2, "items": []}).status_code == 200
    assert client.post("/v1/observations", json={"model": "C", "day": 31,
                                                 "items": [{"segment": "V2-1N", "steer": 99}]}).status_code == 422


def test_world_filter_and_models(client):
    all_segs = client.get("/v1/world").json()["segments"]
    cell = all_segs[0]["cell"]
    some = client.get("/v1/world", params={"cells": cell}).json()["segments"]
    assert 0 < len(some) < len(all_segs) and all(s["cell"] == cell for s in some)
    assert [m["id"] for m in client.get("/v1/models").json()] == ["A", "B", "C", "D", "E", "F"]


def test_demo_history_and_state(client):
    assert client.get("/v1/demo/history").status_code == 404
    body = {"model": "C", "powertrain": "ev",
            "passes": [{"segment": "V2-1N", "day": 20, "trip": 200, "steer": 0.8, "eff": -1.9}]}
    assert client.post("/v1/demo/history", json=body).json() == {"passes": 1}
    assert client.get("/v1/demo/history").json()["passes"][0]["trip"] == 200
    client.post("/v1/observations", json={"model": "C", "day": 31, "items": [{"segment": "V2-1N", "steer": 0.3}]})
    s = client.get("/v1/state", params={"model": "C"}).json()
    assert s["reports"]["steer"] == 1 and len(s["segments"]) == 80


def test_demo_feed_skips_old_samples_and_pages(client):
    smp = {"t": 0.0, "lat": 37.28, "lon": 127.07, "heading": 0.0, "speed_kmh": 80.0, "wheel_deg": 0.02, "consumption": 0.15}
    assert client.post("/v1/demo/feed", json={"samples": [smp] * 3}).json() == {"last": 3}
    first = client.get("/v1/demo/feed").json()
    assert first == {"last": 3, "samples": []}                  # 처음 붙으면 지난 신호는 건너뛴다
    client.post("/v1/demo/feed", json={"samples": [dict(smp, t=1.0), dict(smp, t=2.0, wheel_deg=None)]})
    page = client.get("/v1/demo/feed", params={"after": 3, "limit": 1}).json()
    assert page["last"] == 5 and [x["seq"] for x in page["samples"]] == [4]
    rest = client.get("/v1/demo/feed", params={"after": 4}).json()["samples"]
    assert [x["t"] for x in rest] == [2.0] and rest[0]["wheel_deg"] is None
    assert client.post("/v1/demo/feed", json={"samples": [dict(smp, speed_kmh=-1)]}).status_code == 422


def test_world_has_service_points(client):
    sp = client.get("/v1/world").json()["service_points"]
    assert [p["id"] for p in sp] == ["SP1", "SP2"] and all("시연용" in p["name"] for p in sp)
