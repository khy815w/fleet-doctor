# 차 ↔ 서버 메시지 형식

모든 요청·응답은 JSON(UTF-8)입니다. 날짜(`day`)는 2026-10-01 00:00 KST부터 지난 날 수입니다(`floor((유닉스초 − 1790780400) / 86400)`).
차종(`model`)은 차종 묶음 ID(`A`~`F`)입니다. 지금은 앱 설정값이고(시연 빌드는 `C`), VIN은 읽지도 보내지도 않습니다. VIN으로 자동으로 고르려면 차종 코드표와 Pleos 권한 협의가 필요합니다.

## 차가 보내는 것

### `POST /v1/observations` — 익명 편차 보고

주행이 끝날 때 그 주행의 구간별 편차를 한꺼번에 보냅니다. 차 ID·주행 ID·시각·좌표는 없습니다.

```json
{"model": "C", "day": 31,
 "items": [{"segment": "V2-1N", "steer": 0.12, "eff": -0.013},
           {"segment": "H4-2E", "eff": 0.021}]}
```

- `steer`: 그 구간에서 잰 핸들 각도 평균 − 내 차 평소 값(도). 굽은 구간이거나 아직 모르면 뺍니다.
- `eff`: 1km당 에너지(로그, 속도 보정) − 내 차 평소 값. 아직 모르면 뺍니다.
- 차가 '고장 의심' 상태(누적 점수가 문턱의 절반 이상)인 지표는 보내지 않습니다. 고장 난 차가 길 몫을 흐리지 않게 하려는 것입니다.
- 응답: `{"accepted": 2}`. 모르는 차종은 `400`. 서버 날짜에서 60일보다 오래됐거나 내일보다 뒤인 `day`는 `400`.

## 차가 받는 것

### `GET /v1/world?cells=wydk5,wydk7` — 구간 목록

```json
{"world_id": "demo-grid-v1", "heading_bins": 8,
 "segments": [{"id": "V2-1N", "road": "V2", "heading": 0.0, "straight": true, "length_m": 1500.0,
               "v_ref_kmh": 80.0, "cell": "wydk5", "points": [[37.28, 127.086912], [37.29349, 127.086912]]}],
 "service_points": [{"id": "SP1", "name": "시연용 정비소 1", "lat": 37.294209, "lon": 127.104812}]}
```

- `cells`를 빼면 전부 줍니다. 차는 지오해시 5자리 칸(약 5km)의 주변 9칸을 물어봅니다.
- `service_points`: 경유지로 넣을 정비소. 시연 지도에는 가상의 곳만 있습니다.

### `GET /v1/effects?model=C&day=31&segments=V2-1N,V2-2N` — 오늘의 길 몫

```json
{"model": "C", "day": 31, "server_day": 31,
 "effects": {"V2-1N": {"steer": -0.31, "eff": 0.012, "n_steer": 812, "n_eff": 1430}}}
```

- `steer`, `eff`: 그 구간·차종·날짜의 길 몫 r. 차는 z = y − r 로 '내 차 몫'만 남깁니다.
- `n_*`: 그 구간·차종에 쌓인 보고 수(참고용). 0이면 다른 차종 값을 환산한 추정입니다.

### `POST /v1/effects/query` — 지난 날짜의 길 몫 (평소 값을 다시 계산할 때)

```json
{"model": "C", "items": [{"segment": "V2-1N", "day": 20}, {"segment": "V2-2N", "day": 20}]}
→ {"model": "C", "values": [{"steer": -0.29, "eff": 0.010}, {"steer": -0.41, "eff": 0.004}]}
```

순서는 요청과 같습니다. 한 번에 500개까지.

### `GET /v1/models`

`[{"id": "C", "name": "소형 전기 SUV", "powertrain": "ev"}, ...]`

## 시연 전용 (`FLEETDOC_DEMO=1`일 때만)

- `POST /v1/demo/history`, `GET /v1/demo/history`: 시뮬레이터가 올린 시연 차의 지난 주행 기록.
  `{"model": "C", "powertrain": "ev", "passes": [{"segment": "V2-1N", "day": 20, "trip": 200, "steer": 0.81, "eff": -1.87}, ...]}`
  앱(mock 빌드)은 기록이 없을 때 처음 한 번 받아서, 평소와 같은 순서(주행마다 판단 → 하루 끝에 다시 배우기)로 다시 돌려 상태를 만듭니다.
- `GET /v1/state?model=C`: 관제 화면용 요약(구간별 길 몫, 날짜별 몫, 차종 환산 비율, 보고 수).

## 에뮬레이터 시연 신호 (mock 빌드)

mock 빌드는 Pleos SDK 대신 시연 서버 통로에서 차량 신호를 받습니다. `simulator/drive_emulator.py`가 쌓고 앱이 받아 갑니다.

- `POST /v1/demo/feed` `{"samples": [{"t": 12.0, "lat": 37.28, "lon": 127.0869, "heading": 0.0, "speed_kmh": 81.2, "wheel_deg": 0.04, "consumption": 0.152}, ...]}` → `{"last": 1234}`
  - `t`: 시연 시계(초). 실제보다 빨리 갈 수 있습니다(시연은 25배).
  - `wheel_deg`: 앞바퀴 각도(도, 오른쪽 +). Pleos SDK가 주는 값과 같은 꼴이라, 앱이 기준 조향비 15를 곱합니다.
  - `consumption`: 지금 1km당 에너지(kWh/km 또는 L/km). 서 있을 때는 `null`.
- `GET /v1/demo/feed?after=1200&limit=500` → `{"last": 1234, "samples": [{"seq": 1201, ...}, ...]}`. `after`가 0보다 작으면 표본 없이 마지막 번호만 줍니다(앱을 켜기 전에 쌓인 신호는 건너뜁니다).

음성 대답과 버튼은 방송(broadcast, `-p com.fleetdoctor`)으로 흉내 냅니다.

| 동작(action) | 값 | 뜻 |
| --- | --- | --- |
| `com.fleetdoctor.ASK` | 없음 | "내 차 괜찮아?" 버튼과 같음 |
| `com.fleetdoctor.ANSWER` | `text`(string) | 음성 인식 결과를 흉내 냄. "경유지로 넣을까요?"에 대한 대답(예: "응", "아니") |
| `com.fleetdoctor.SERVICED` | 없음 | "정비했어요" 버튼과 같음: 평소 값을 처음부터 다시 배움 |
