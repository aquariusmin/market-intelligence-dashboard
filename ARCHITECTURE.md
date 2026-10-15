# Architecture

- **기준:** 2026-10-15, `feature/v1.0-local` 작업 트리
- **문서 역할:**
  - 이 문서는 개발자가 참고할 **현재 구조와 그 구조를 지키는 규칙**을 정리합니다.
  - 설치·실행·데이터 약관 표는 `README.md`에 있습니다.
  - 리뷰 결과는 `CODE_REVIEW.md`, 다음 단계는 `ROADMAP.md`, 장기 구상(Next.js·FastAPI)은 `BLUEPRINT.md`에 있습니다.

## 1. 한눈에 보기

```text
                 ┌──────────────── 수집 (네트워크) ───────────────────────────────┐
FRED SP500/NDX ──┤ scripts/collect_data.py + providers.py  (v0.2, stdlib + curl)   │
KRX OPEN API ────┤        └─> data/markets.json  (v0.2 스냅샷)                      │
Fed DDP H.15/H.10┤ mi/macro.py ──────────────┐                                     │
Fed·SEC RSS,GDELT┤ mi/news.py, mi/events.py  │                                     │
                 └───────────────────────────┼─────────────────────────────────────┘
                                             ▼
                    data/mi.sqlite  (추가 전용 · 라이선스 게이트 storage_allowed)
       prices · macro · observation_revisions · forecasts · backtest_runs/predictions
       holdout_registry · news · briefings · daily_snapshots · krx_calendar · provider_calls · job_runs
                (PRAGMA user_version=4, WAL)
                                             │
     ┌──────────── 분석 (오프라인, 결정적) ──┼─────────────────────────────────────┐
     │ timing ─ indicators ─ regime          │  licenses (ml_allowed 게이트)        │
     │ dataset ─ models ─ validation ─ forecasting                                  │
     │ sentiment ─ briefing (규칙 기반 / 옵트인 LLM + validate_llm)                │
     └──────────────────────────────────────┼─────────────────────────────────────┘
                                             ▼
             scripts/mi_cli.py  ──>  data/dashboard/*.json  (원자적 교체)
                                             ▼
   index.html (v0.2 UI + DEMO) ← real-data.js (markets.json) ← intel.js (dashboard/*.json)
```

**설계 성격:**
- 서버 없이 1인이 로컬에서 쓰는 구조입니다.
- 화면 쪽에 빌드 단계가 없습니다. 브라우저는 정적 JSON만 읽습니다.
- 계산은 모두 Python 배치에서 합니다. 브라우저에서는 계산하지 않습니다.

## 2. 구성요소

### 2.1 수집 계층
| 모듈 | 입력 | 출력 | 비고 |
|---|---|---|---|
| `collect_data.py`, `providers.py` | FRED CSV, KRX `idx/{kospi,kosdaq}_dd_trd` | `markets.json` | v0.2 인터페이스를 **변경 금지**. curl을 하위 프로세스로 쓰고, 키는 stdin으로만 넘기고, 재시도는 3회. `--output`으로 경로를 바꿀 수 있음 |
| `mi/backfill.py` | KRX OPEN API(기준일별) | `prices`, `krx_calendar` | v0.5 장기 이력. 이어 받기, 하루 예산, 오류 시 중단(진행분 보존). 어제(KST)까지만 요청 |
| `mi/prices.py` | `markets.json` | `prices` 테이블 | `storage_allowed()`를 통과한 소스만 보관(현재 KRX만). FRED는 읽기만 |
| `mi/macro.py` | Fed DDP CSV(H.15 3개, H.10 2개). 옵트인 시 FRED VIX·ICE | `macro` 테이블 / `fred_risk.json`(TTL 3일) | 5년 단위로 나눠 받고, 범위를 검사하고, 개정 이력을 기록 |
| `mi/news.py` | Fed·SEC RSS, GDELT DOC | `news` 테이블 | 메타데이터만 저장. 중복은 삭제하지 않고 `duplicate_of`로 연결. 429를 받으면 나머지 쿼리를 중단 |
| `mi/events.py` | FOMC 일정 페이지, `data/events.local.json` | 브리핑 입력 | 연도 넘김을 처리 |

### 2.2 분석 계층
| 모듈 | 책임 | 지켜야 할 규칙 |
|---|---|---|
| `mi/timing.py` | `available_at`, `decision_time`, `asof_join`, Fed 휴일, 소스별 가격 공개 규칙 | 모든 외부 값은 이 모듈을 거쳐 결합합니다. 동률은 최신 관측일을 고릅니다(mergesort) |
| `mi/indicators.py` | SMA·RSI(Wilder)·MACD·실현변동성·낙폭·과거 구간 백분위 | v0.2 JS 공식과 수치가 같아야 합니다(테스트). 인과적 계산만 하고, `min_periods`를 반드시 지정 |
| `mi/regime.py` | 추세 규칙 5개 → 점수 −5..+5, 변동성 백분위 ≥ 0.70, 3일 확인 → 5개 국면. 0–100 위험 점수, 요인 문장 | 규칙은 사전에 고정합니다(데이터에 맞춰 조정하지 않음). 이력은 그 시점까지의 데이터로만 계산한 기록입니다. 위험 점수는 사용한 구성요소 수를 함께 보고 |
| `mi/daily.py` | v0.4 전일 대비 변화(`diff`), 거시 변화, 국면 해설, 스냅샷 행 | 기준값은 사전 고정(`daily-v1`). 화면용 `text`와 수치 없는 `plain`을 함께 생성. 위험 점수는 구성요소 집합이 같을 때만 비교 |
| `mi/explain.py` | v0.6 전망 변화 설명: 같은 모델로 직전 입력과 오늘 입력을 비교, 입력 하나씩 되돌려 분해 | 검증 통과 모델에만 생성. 인과 아님을 명시. 상호작용 몫 별도 |
| `mi/nlp_eval.py` | 사용자 라벨 대비 뉴스 분류·감성 정확도 | 재내보내기 시 라벨 보존. 판정 기준 사전 고정(라벨 100개 이상, macro-F1 0.5 이상) |
| `mi/licenses.py` | `SOURCES` 레지스트리, `ml_allowed`, `storage_allowed`, 사용자 manifest | 확인되지 않은 권한은 불허. 조건부 저장은 `conditions`를 적어야 통과 |
| `mi/dataset.py` | 자기 시장 피처 13개, 거시·교차시장 피처, 3분류 라벨(neutral 대역 k=0.43·σ60) | 라이선스를 통과하지 못한 입력의 피처는 제외(`dropped_features`). 한국 예측은 미국 t−1 값만 사용 |
| `mi/models.py` | `class_frequency`, `momentum_rule`, LR, LightGBM. 보정 none·temperature·sigmoid·isotonic | 확률 행의 합 = 1, 모든 값 > 0 |
| `mi/validation.py` | purged·embargo walk-forward(expanding/rolling), 개발/holdout, 지표, 블록 부트스트랩, 검증 기준 | `GATE_FIELDS`는 런타임에 변경할 수 없습니다. 두 기준 모델 모두에 대해 CI 하한 > 0. holdout 시작일은 고정 |
| `mi/forecasting.py` | 학습 실행, 실시간 예측 저장, 만기 판정, `live_summary` | 백테스트와 실시간 예측을 분리. 예측은 추가만 하고, 집계는 (시장·기간·기준일)마다 1건 |
| `mi/sentiment.py`, `mi/briefing.py` | 영문 감성 사전(`lexicon-en-v2`), 8개 섹션 한국어 브리핑, LLM 재작성 | 화면용과 보관용(`restricted`) 두 벌을 만듭니다. SQLite와 LLM 입력에는 보관용만 씁니다. LLM 출력은 `validate_llm`(숫자·시장·부호·어휘)을 통과해야 하고, 실패하면 규칙 기반 문장으로 대체 |

### 2.3 저장 계층 — `mi/store.py`
| 테이블 | 키 | 쓰기 방식 |
|---|---|---|
| `prices` | (market, date, source_id) | upsert. 값이나 소스가 바뀌면 `observation_revisions`에 기록 |
| `macro` | (series_id, date) | 같음 |
| `forecasts` | id, UNIQUE(market, horizon, model_version, input_cutoff) | 추가 전용. 판정할 때 결과 칼럼만 채움 |
| `backtest_runs` / `backtest_predictions` | run_id(마이크로초 포함) | 추가 전용 |
| `holdout_registry` | (market, horizon) | 처음 한 번만 기록 |
| `news` | id(정규화한 URL 해시) | 추가와 중복 연결 |
| `briefings` | id | 추가 전용. 보관용 사본: 보관이 허용되지 않은 소스의 가격 파생 수치는 빼고 라벨만 저장(`redacted_markets`) |
| `daily_snapshots` | UNIQUE(market, as_of, method, payload_hash) | 추가 전용, 멱등. 보관 불가 소스 시장은 라벨만 저장. 감사 기록일 뿐 계산 입력이 아님 |
| `krx_calendar` | (market, date) | 백필 응답 기록(trading / no_data). 이어 받기와 관측 거래일 달력 |
| `provider_calls` | (provider, day_kst) | KRX 하루 호출 수(실행 간 누적, 예산 8,000/한도 10,000) |
| `job_runs` | id | running → ok/partial/failed. 6시간이 지나면 interrupted |

- **연결 설정:** WAL, `timeout=60s`, `PRAGMA user_version`. 마이그레이션은 `_migrate()`에 단계를 추가하는 방식으로만 합니다.
- **이식성:** 표준 SQL 타입만 씁니다. PostgreSQL로 옮길 수 있습니다.

### 2.4 오케스트레이션 — `scripts/mi_cli.py`
- **단일 명령:** `ingest` · `regime` · `train` · `forecast` · `news` · `briefing` · `status` · `backup` · `backfill` · `label-export` · `label-score`
- **복합 명령:**
  - `morning` / `evening`: ingest → regime → forecast → news → briefing
  - `weekly`: ingest → regime → train → forecast
  - `all`
- **실패 격리:**
  - 단계마다 try/except로 감싸고 traceback을 출력합니다.
  - 실패한 단계 이름이 다음 단계로 전달됩니다(`failed_stages` → 브리핑의 `input_warnings`).
  - `status`는 항상 마지막에 따로 격리해서 실행합니다.
  - 하나라도 실패하면 **exit 1**입니다.
- **쓰기 순서:** DB commit 다음에 JSON을 원자적으로 교체합니다(`atomic_json`). 그래서 화면에 보이는 JSON이 DB보다 앞서는 일이 없습니다.
- **데이터 경로:** `MI_DATA_DIR`가 모든 경로(`paths.py`)를 바꾸고, 가격 수집기에도 `--output`으로 전달합니다. 실데이터와 완전히 격리됩니다.
- **스케줄:** `scripts/cron_run.sh`가 환경 파일(`~/.config/mi/env`)을 읽고, `flock`으로 중복 실행을 막습니다.

### 2.5 화면 계층
| 파일 | 역할 | 규칙 |
|---|---|---|
| `index.html` | v0.2 UI와 DEMO 데이터(고정 샘플) | DEMO와 REAL은 화면에서 명확히 구분 |
| `real-data.js` | `data/markets.json` 로드, 스키마 검증 → v0.2 REAL 화면 | v0.2 동작 유지 |
| `scripts/serve.py` | 로컬 서버 (`make dev`, `ui_smoke.py`) | 127.0.0.1 전용. 허용 파일 5종만 제공. Host 검사(421), CSP·nosniff·no-referrer |
| `intel.js` | `data/dashboard/*.json` 로드 → 6개 탭 패널 | 모든 문자열은 `esc()`, URL은 `safeUrl()`. `INTEL_SHAPE`로 형식 검사, 패널별 `guard()`, STALE 배지와 재로드 실패 배지. 미검증 확률은 "신뢰할 수 없음" |

**JSON 계약** (`data/dashboard/`, 모두 `generated_at` 포함):

| 파일 | 상위 키 | 생산자 |
|---|---|---|
| `regime.json` | engine_version, method, note, markets{k: status, as_of, regime, raw_regime, confirm_days, trend_score, trend_rules, regime_since/days, metrics, risk, bullish/bearish_factors, history, vol_history, transitions, counts, source_id, data_status} | `cmd_regime` |
| `macro.json` | series{id: …} | `cmd_regime` |
| `performance.json` | engine_version, gate, markets, licenses | `cmd_train` |
| `forecast.json` | markets, resolved_this_run, history, live_summary | `cmd_forecast` |
| `news.json` | report, items, sentiment_note, model_licenses, separation_note | `cmd_news` |
| `briefing.json` | session, method, sections, sources, disclaimer, events_status, next_fomc, llm_status | `cmd_briefing` |
| `ingest.json` / `status.json` | 수집 결과 / 전체 상태·작업 로그·라이선스 | `cmd_ingest` / `cmd_status` |

**스키마 변경 규칙:**
- 키는 **추가만** 합니다. 이름 변경이나 삭제가 필요하면 `intel.js`가 이전 형태와 새 형태를 모두 읽을 수 있게 먼저 바꾼 뒤에 합니다.
- 변경 후에는 `dashboard-qa` Skill과 `ui_smoke.py`를 실행합니다.

## 3. 시간 모델

| 대상 | `available_at` (UTC로 저장) |
|---|---|
| 미국 지수 종가 (FRED) | 16:00 ET + 15분 |
| KRX 지수 종가 | 다음 평일 08:00 KST (보수적. 한국 휴장일 미반영 → ROADMAP 0.5-B) |
| 라이선스 데이터 | 종가 + manifest `publication_delay_minutes`. 값이 없으면 다음 영업일 09:00(현지) |
| H.15 금리 | 다음 연준 영업일 17:00 ET |
| H.10 환율 | 해당 주 다음의 화요일(휴일 반영) 16:15 ET |
| FRED VIX·ICE | 다음 평일 18:00 ET |

- **결정 시각:** `decision_time` = 각 시장 마감 + 30분
- **as-of 조인 규칙:** 피처는 `available_at ≤ decision_time`인 값 중 가장 최신 값만 씁니다.
- **서머타임:** `zoneinfo`가 처리합니다.

## 4. 라이선스 게이트 흐름

```text
source_id ─> licenses.source() ─┬─ storage_allowed? ── 아니오 ─> SQLite에 쓰지 않음 (news는 skipped_unlicensed)
                                └─ ml_allowed? ─────── 아니오 ─> dataset에서 해당 피처 제외 / 대상 시장 unavailable(사유)
```

- 현재 학습에 허용된 가격 소스가 없으므로, 12개 예측 조합이 모두 `unavailable`입니다(정상).
- 새 소스는 코드를 바꾸지 않고 `SOURCES` 또는 manifest에 **조항과 확인일**을 적는 것만으로 활성화됩니다.

## 5. 검증 파이프라인 (학습 1회)

```text
dataset.build ─> 개발 구간 ─┬─ fold: fit → purge(h)+embargo → calibration → purge → test   (반복, step=21)
                            │   └─ 하이퍼파라미터·보정 방법 선택 (개발 구간 안에서만)
                            └─ purge ─> holdout (holdout_registry에 고정된 시작일 이후)
                                         └─ 지표 + 블록 부트스트랩 CI ─> gate ─> VALIDATED / NOT VALIDATED
```

검증 기준(전부 충족해야 통과, 2026-10-15 고정):
- 유효 holdout n ≥ 50 (AR(1) 보정 적용)
- `class_frequency`와 `momentum_rule` **둘 다**에 대해 log loss 개선과 Brier 개선의 90% CI 하한 > 0
- 균형 정확도 개선 ≥ 0.02
- 최악 클래스 ECE ≤ 0.08
- 개발 대비 holdout log loss 악화 ≤ 10%

## 6. 테스트 구조

| 범위 | 파일 | 수 | 실행 |
|---|---|---|---|
| v0.2 수집기 | `scripts/test_data.py` | 12 | `python3 test_data.py` (stdlib) |
| 엔진 | `test_mi_forecasting.py`, `test_mi_analysis.py` | 78 | `.venv` unittest |
| CLI·로컬 서버 | `test_mi_cli.py` | 14 | 같음 |
| 화면 로직 | `scripts/test_intel.mjs` | 8 | `node --test` (vm으로 index.html 인라인 → real-data.js → intel.js 로드) |
| 브라우저 | `scripts/ui_smoke.py` | 24 뷰 | headless Chromium |
| 데이터 감사 | `.claude/skills/financial-data-audit/scripts/audit_data.py` | — | 읽기 전용 |

- 네트워크를 쓰는 단위 테스트는 없습니다(공급자는 모두 mock).
- 합성 데이터는 테스트 안에서만 씁니다.
- Python 커버리지는 89%입니다(2026-10-15, v0.4 반영 후). JS는 순수 함수와 로더를 단위 테스트하고, 렌더링은 브라우저 스모크로 검사합니다.

## 7. 확장 지점 (ROADMAP과 대응)

| 하고 싶은 일 | 손댈 곳 | 손대지 말 것 |
|---|---|---|
| 새 데이터 소스 | `licenses.SOURCES`(조항·확인일) → 수집 모듈 → `timing`의 공개 규칙 → 감사 | 게이트 우회, 추정값 채우기 |
| 일별 스냅샷 (v0.4) | `store`의 스키마 v3, 새 `mi/daily.py`, `regime.json`에 `daily` 블록 추가 | 새 JSON 파일이나 새 서버 |
| 실데이터 예측 (v0.5) | 데이터 소스 등록, KRX 백필 옵션, 휴장일 | `models`·`validation` 재작성, `GATE_FIELDS` 변경 |
| 전망 변화 설명 (v0.6) | 새 `mi/explain.py` → 브리핑 FACTS | 미검증 모델의 설명 생성, 뉴스를 피처로 사용 |

## 8. 보안 경계

| 경계 | 통제 |
|---|---|
| 비밀정보 → 프로세스 | 환경변수만 사용. cron은 소유자 전용 환경 파일만 읽음 |
| 프로세스 → 공급자 | curl HTTPS 전용(리다이렉트 포함). 키는 stdin config로 전달. 인증 요청은 리다이렉트 안 함. 응답 크기 10MB 제한 |
| 공급자 응답 → 저장 | 형식·범위 검증, 라이선스 게이트, 메타데이터만 저장 |
| 저장 → 브라우저 | `serve.py` 허용 목록. SQLite·로그·소스는 제공하지 않음 |
| JSON → DOM | `esc()`·`safeUrl()`, CSP(`connect-src 'self'`) |
| 브리핑 → 외부 LLM | 보관용 사본만 전송(보관 불가 소스 수치 제외). 출력은 `validate_llm` 통과 필수 |
| 디스크 | JSON·SQLite 0600, cron `umask 077`, `data/`는 gitignore |

## 9. 아키텍처 결정 기록 (요약)

| # | 결정 | 이유 | 재검토 조건 |
|---|---|---|---|
| A1 | 정적 JSON + 빌드 없는 Vanilla JS | 1인 로컬 사용, v0.2 화면 보존 | 다중 사용자 또는 외부 배포가 필요할 때 |
| A2 | SQLite 단일 파일, 추가 전용 | 이력 보존, 백업이 쉬움 | 동시 쓰기가 많아질 때 |
| A3 | v0.2 수집기를 하위 프로세스로 호출 | v0.2 인터페이스 보존 | v0.2 화면을 은퇴시킬 때 |
| A4 | 라이선스를 코드 레지스트리로 관리 | 테스트할 수 있는 게이트 | 소스가 20개를 넘을 때 |
| A5 | 국면은 규칙 기반, 예측과 분리 | FRED 가격은 ML 적합 금지, 해석 가능성 | 라이선스 데이터를 확보했을 때(HMM 검토) |
| A6 | LLM은 문장 재작성만 하고 수치는 검증 | 숫자를 지어내지 않게 하기 위해 | — |
