# Code Review — Market Intelligence Dashboard

- **대상:** `feature/v1.0-local` 브랜치 작업 트리 (기준 커밋 `39913b6` + 미커밋 수정)
- **작성일:** 2026-10-15
- **범위:** 1차 리뷰(수정 전)와 그 결과로 적용한 수정, 그리고 이번 특별 검증 항목. 같은 문제를 여러 도구가 지적한 경우 하나로 합쳤습니다.

> 요청서에는 현재 단계가 "v0.3"으로 적혀 있습니다. 하지만 저장소의 실제 코드는 README H절 기준 v1.0 로컬 엔진(`scripts/mi/`)까지 들어 있습니다(CLAUDE.md 참고). 이 문서는 **코드에 실제로 있는 것**을 기준으로 리뷰합니다. 버전 번호를 어떻게 맞출지는 `ROADMAP.md` §0을 보세요.

---

## ⚠ Critical 요약 (먼저 읽을 것)

| ID | 분류 | 문제 | 상태 |
|---|---|---|---|
| **C1** | 라이선스 | FRED 원시 시세(S&P 500·NASDAQ 100 약 800일치)가 `data/markets.json`에 디스크로 저장됨 | **위험 수용 · 문서화** (D1). 라이선스 데이터 확보 시 교체 |
| **C2** | 라이선스 | FRED 가격에서 계산한 수치(수익률·변동성·낙폭·위험 점수)가 SQLite `briefings.content`에 영구 보관되고, 옵트인 LLM 입력으로도 외부에 전송됨 | **수정됨** (D2). 이후 저장분과 LLM 입력에서 제외 |
| C3 | 잘못된 계산 | LLM 브리핑의 수치 검증이 숫자 집합만 비교해, 시장이 뒤바뀐 수치·부호 반전·예측 문장이 통과함 | 수정됨 |
| C4 | 데이터 손상 | `MI_DATA_DIR` 격리 실행에서도 가격 수집기가 **실데이터** `data/markets.json`을 덮어씀 | 수정됨 |
| C5 | 잘못된 계산 | `asof_join` 동률 처리가 불안정 정렬이라, 같은 시각에 공개된 H.10 값 중 임의 날짜 값이 선택됨 (합성 데이터 1,827행 중 680행) | 수정됨 |

C1과 C2는 코드 버그가 아닙니다. FRED 약관과 기존 동작(원칙 6: v0.2 동작 유지)이 서로 부딪히는 문제입니다. 사용자가 결정을 위임했고(2026-10-15), 적용한 결정은 §4에 있습니다. 보안 점검 결과는 §3.10에 있습니다.

---

## 1. 리뷰 방법

| 도구 | 용도 | 결과 |
|---|---|---|
| 프로젝트 Skill 6종 (`time-series-validation`, `quant-model-review`, `financial-data-audit`, `market-regime-analysis`, `financial-news-nlp`, `dashboard-qa`) | 영역별 리뷰. 병렬 서브에이전트로 실행 | 아래 발견 사항의 대부분 |
| 일반 버그·보안 리뷰 (`code-review` 관점) | 오케스트레이션, 예외 처리, XSS, 비밀정보 | C4, H2, H4 등 |
| `context7` | scikit-learn 문서 확인 | `LogisticRegression(C=…)`가 1.10부터 deprecated → L6 |
| `claude-api` Skill | LLM 호출 파라미터 확인 | 호출 형식은 유효. 다만 `anthropic` 패키지가 빠져 있었음 → H9 |
| WebFetch | FRED·KRX·SEC 약관 원문 대조 | §3.1, §3.2 |
| `coverage` (스크래치 venv, 프로젝트 `.venv`는 건드리지 않음) | Python 커버리지 측정 | §3.8 |
| headless Chromium (`scripts/ui_smoke.py`) | 실제 브라우저 렌더링 | §3.9 |
| **사용하지 못함** | `pyright-lsp`(바이너리가 PATH에 없음), data 플러그인 `definite` MCP(연결 실패) | 정적 타입 검사는 하지 못했습니다 |

**표기:**
- **확인** = 코드 대조나 재현으로 확인된 문제
- **추정** = 코드상 가능성은 있지만 실제 피해나 외부 사실(공급자의 실제 게시 시각 등)을 확인하지 못한 위험

---

## 2. 발견 사항

### Critical

**C1. FRED 원시 시세의 디스크 저장 — 확인 · 위험 수용(D1)**
- **근거:**
  - `scripts/providers.py: fred()` — 800일치를 받습니다.
  - `scripts/collect_data.py` — `data/markets.json`에 기록합니다(v0.2 스냅샷).
  - `scripts/mi/regime.py: analyze()` → `data/dashboard/regime.json`의 260일 국면 이력.
- **약관 원문** (https://fred.stlouisfed.org/legal/, 2026-10-15 확인):
  - "Store, cache, or archive any portion of the FRED® Services or FRED® Content"
  - "Use the FRED® Services or FRED® Content in connection with the development or training of any software program … machine learning"
  - 저작권 표시가 있는 시리즈(S&P, Nasdaq)는 "you must contact the data owner to obtain permission"
- **완화된 부분:**
  - SQLite `prices`에 FRED 행은 0개입니다.
  - ML 게이트가 FRED를 차단합니다(`licenses.ml_allowed`).
  - `--with-fred-risk`로 받는 VIX·ICE(`fred_risk.json`)는 3일이 지나면 무시하고 삭제합니다(`mi_cli.transient_risk`).
- **남은 문제:** v0.2 스냅샷 자체는 약관의 "store/cache/archive" 금지와 충돌할 소지가 있습니다.
- **결정(D1):** 원칙 6(v0.2 동작 유지)에 따라 유지합니다. 위험은 README B절에 명시했습니다. 미국 지수의 라이선스 데이터를 확보하면 교체합니다.

**C2. FRED 파생 수치의 SQLite 영구 보관·외부 전송 — 확인 · 수정됨 (신규)**
- **근거:** `scripts/mi/briefing.py: market_paragraph()`가 S&P 500·NASDAQ 100의 20/60거래일 수익률, 실현변동성, 52주 낙폭, 위험 점수를 문장으로 만듭니다. `mi_cli.cmd_briefing`은 이 문장을 `store` `briefings.content`에 추가 전용으로 저장합니다.
- **실제 확인:** 현재 `data/mi.sqlite`에 2건이 있고, 예를 들어 "20거래일 수익률 +2.21% … 20일 실현변동성 10.3%"가 들어 있습니다.
- **README와의 불일치:** README B절의 "FRED 시세는 SQLite에 복사하지 않는다"는 원시 시세에 한해서만 맞습니다.
- **해석이 필요한 부분:** 파생 통계가 약관의 "FRED Content"에 해당하는지는 법적 해석 문제입니다. 보수적으로 보면 충돌 가능성이 있습니다.
- **원칙 간 충돌:** 원칙 5(이력 보존) 때문에 이미 저장된 행을 삭제하는 것도 사용자 승인 사항입니다.
- **같은 문제의 다른 경로:** 옵트인 LLM(`briefing.llm_rewrite`)이 같은 수치를 FACTS로 Anthropic API에 보냈습니다. 학습은 아니지만, 재배포(`not_allowed`)에 해당할 수 있는 외부 전송입니다.
- **수정:**
  - `briefing.build(..., restricted=…)`로 보관용 사본을 따로 만듭니다. `storage_allowed()`를 통과하지 못한 소스의 시장은 국면·위험 등급 라벨만 남기고, 요인은 "요인 n개 (보관하지 않음)"으로 바꿉니다.
  - `mi_cli.cmd_briefing`은 이 사본을 SQLite에 저장하고 LLM FACTS(`facts_brief`)로 씁니다. 화면용 `briefing.json`은 그대로입니다.
  - 기존 2건은 삭제하지 않았습니다(원칙 5).
- **테스트:** `test_restricted_build_keeps_labels_but_no_price_derived_numbers`, `test_briefing_archive_and_llm_input_exclude_fred_derived_numbers`, `test_llm_is_opt_in_and_validated`(LLM이 받는 입력 확인). 실데이터 사본으로 끝까지 실행해 확인했습니다.

**C3. LLM 수치 검증 우회 — 확인(재현) · 수정됨**
- **근거:** `briefing.validate_llm()`
- **수정 전:** 출력의 숫자가 입력 FACTS 숫자 집합에 있기만 하면 통과했습니다. 그래서 "NASDAQ 위험 26.9점, S&P 38.6점"처럼 시장을 뒤바꾼 문장이 통과했습니다.
- **수정 후:** 아래 경우 모두 거부합니다.
  - 숫자와 시장명의 짝이 FACTS와 다름
  - 부호가 바뀜
  - 예측·투자 권유 어휘를 씀
  - "두 배" 같은 배수 표현을 씀
- **테스트:** `test_mi_analysis`의 적대적 입력 테스트

**C4. `MI_DATA_DIR` 격리 실패 — 확인 · 수정됨**
- **근거:** `mi_cli.cmd_ingest`
- **수정 전:** 수집기를 `--output` 없이 호출해서, 스크래치 디렉터리에서 실험해도 실제 `data/markets.json`이 바뀌었습니다.
- **수정 후:** `--output paths.MARKETS_JSON`을 넘깁니다.
- **테스트:** `test_mi_cli.test_ingest_writes_collector_output_inside_data_dir_and_isolates_archive_errors`

**C5. as-of 조인 동률 처리 — 확인(합성 데이터로 재현) · 수정됨**
- **근거:** `timing.asof_join`
- **문제:** H.10은 한 주치 값이 같은 시각에 공개됩니다. 불안정 정렬 때문에 이 중 가장 최신이 아닌 값이 선택될 수 있었습니다. 미래 값을 쓰지는 않으므로 누출은 아닙니다. 하지만 백테스트와 실시간 예측이 서로 다른 값을 쓸 수 있었습니다.
- **수정:** `sort_values(['available','obs_date'], kind='mergesort')`로 정렬합니다.
- **테스트:** `test_mi_forecasting`의 동률 테스트

### High

| ID | 문제 | 근거 (파일 · 함수) | 확인/추정 | 상태 |
|---|---|---|---|---|
| H1 | 검증 게이트가 가장 약한 기준 모델(`class_frequency`)과만 비교했습니다. holdout 시작점도 실행할 때마다 다시 계산했고, Brier 점수에는 신뢰구간 조건이 없었고, 블록 부트스트랩 블록이 짧았습니다 | `validation._compare`, `validation.gate`, `Config` | 확인 | 수정됨. 이제 최강 기준 모델과도 비교하고, holdout 시작일을 `holdout_registry`에 고정하고, Brier 90% CI 하한 > 0을 요구하고, 블록 길이를 `max(2h, n^(1/3), 10)`로 바꿨습니다. ECE는 최악 클래스 기준이고, 개발→holdout 과적합 간격을 확인하며, `GATE_FIELDS`는 실행 중에 덮어쓸 수 없습니다. **실제 학습 결과가 하나도 없는 시점(2026-10-15)에 고정했습니다** |
| H2 | 복합 작업의 마지막 status 단계가 실패 격리 밖에 있었습니다. 일부 단계가 실패해도 종료 코드가 0이었습니다 | `mi_cli.run_all`, `main` | 확인 | 수정됨. 단계별로 격리하고 traceback을 출력하며, 하나라도 실패하면 exit 1 |
| H3 | 실시간 성과를 최근 400행만 집계했습니다. 재학습으로 모델 버전이 바뀌면 같은 기준일 예측이 중복으로 집계되었습니다 | `forecasting.live_summary`, `evaluated_live` | 확인 | 수정됨. 전체 이력을 쓰고, (시장·기간·기준일)마다 첫 예측 하나만 셉니다. 균등 분포 대비 skill 점수와 경고를 추가했습니다 |
| H4 | SQLite가 WAL이 아니었고, 시장 단위 commit도, cron 중복 실행 방지도 없었습니다 | `store.connect`, `schedule.example.cron` | 확인 | 수정됨. WAL, `timeout=60`, 시장마다 commit, `scripts/cron_run.sh`의 `flock`. 6시간 넘게 `running`인 작업은 `interrupted`로 바꿉니다 |
| H5 | H.15 게시 시각 규칙이 미국 공휴일을 무시했습니다 | `timing.MACRO_LAG`, `_next_weekday` | 추정 (공휴일 다음 날 아직 게시되지 않은 값을 쓸 가능성) | 수정됨. `fed_holidays()`, `is_us_business_day()`, `_h10_release()` 추가 |
| H6 | 가격 공개 지연을 모든 소스에 15분으로 일괄 적용했습니다 | `timing.price_available_at` | 추정 (KRX가 다음 날 아침에 게시하면 백테스트에 약한 누출) | 수정됨. KRX는 다음 평일 08:00 KST로 봅니다. 라이선스 데이터는 manifest의 `publication_delay_minutes`를 쓰고, 값이 없으면 다음 영업일 09:00으로 봅니다 |
| H7 | FOMC 일정에서 12월→1월 연도 넘김을 잘못 처리했습니다 | `events.py` | 확인 | 수정됨 |
| H8 | UI가 JSON 재로드에 실패해도 이전 데이터를 경고 없이 보여줬습니다. 오래된 데이터 표시도 없었고, 형식이 깨진 파일 하나가 전체 패널을 망가뜨렸습니다 | `intel.js` 로더, `INTEL_SHAPE` | 확인 | 수정됨. STALE 배지, "재로드 실패 · 이전 데이터" 배지, 패널별 `guard()` |
| H9 | `anthropic` 패키지가 없어서 `--llm`이 항상 규칙 기반 문장으로 대체되었습니다 | `requirements.txt`, `briefing.llm_rewrite` | 확인 | `requirements-optional.txt`로 분리. **유료 호출이라 실제로 실행하지 않았습니다** |
| H10 | 조건부(`conditional`) 저장 권한이 실제 조건 기재 없이도 통과했습니다. GDELT 제목의 ML 학습이 허용되어 있었습니다 | `licenses._storage_ok`, `SOURCES['gdelt_doc']` | 확인 | 수정됨. `conditions`를 적어야 통과하고, GDELT ML은 `conditional`(학습 불허)입니다 |
| H11 | 뉴스를 저장하기 전에 `storage_allowed()`를 확인하지 않았습니다 | `news.collect` | 확인 | 수정됨. `skipped_unlicensed`를 리포트에 남깁니다 |
| H12 | 실데이터 첫 실행: 백필이 일시적 오류로 중간에 멈춘 상태에서 학습·예측이 실행되어, 4년 전 입력(2022-10-04)으로 만든 "실시간" 예측 3건이 저장됨. 다음 판정 때 이미 알려진 결과로 채점되어 실시간 성과가 오염될 수 있었음 | `forecasting._forecast_one`, `evaluated_live`, `backfill.run` | 확인(2026-10-16) | 수정됨. 입력 7일 초과 시 예측 거부, 다음 개장 이후 기록은 집계 제외(`is_timely`, 행은 보존), 백필 일시 오류 재시도. 기존 3건은 "늦은 기록"으로 표시 |

### Medium

| ID | 문제 | 근거 | 확인/추정 | 상태 |
|---|---|---|---|---|
| M1 | 뉴스 분류 정규식이 단어 경계를 보지 않아 오탐이 있었습니다("important"→Trade, "Warner"→Geopolitics, "경기도"→Economic Growth) | `news.categorize` | 확인 | 수정됨 |
| M2 | 중요도가 같은 뉴스는 오래된 기사부터 나왔습니다. 경과 시간 감점이 수집 시점에 고정되었습니다 | `news.importance`, `briefing.build` | 확인 | 수정됨. 저장 값은 그대로 두고 읽을 때 감점합니다(`effective_importance`) |
| M3 | 감성 분석이 부정어("not", "no")를 무시했습니다 | `sentiment.py` | 확인 | 수정됨. 방법 이름을 `lexicon-en-v2`로 바꿨습니다(D3). 기존 뉴스 행의 `v1` 기록은 그대로 둡니다 |
| M4 | 위험 점수가 들어온 구성요소만으로 평균을 냅니다. 지금 sp·nq는 5개 중 3개로 계산되므로, VIX가 들어오는 날 시장 상황과 무관하게 점수가 크게 바뀝니다 | `regime.risk_level` | 확인 | **일부만 해결.** 구성요소 수(`components_available/missing`)를 표시하게 했습니다. 구성이 다른 날끼리의 비교 문제는 v0.4 과제입니다(`ROADMAP.md` 0.4-B) |
| M5 | 금리 방향이 위험 점수와 요인 문장에서 서로 다르게 처리되었습니다. 같은 화면에 10년물 20일 변화가 +24bp와 +28bp로 따로 나왔습니다(기준일 차이) | `regime.factors` | 확인 | 수정됨. 요인 문장에 기준일 "(기준 MM-DD)"을 표시합니다 |
| M6 | `mi_cli` 테스트가 하나도 없었습니다 | `scripts/test_mi_cli.py` | 확인 | 7개 추가. 그래도 커버리지가 59%로 가장 낮습니다(§3.8) |
| M7 | JavaScript 단위 테스트가 없습니다 | `intel.js`, `real-data.js` | 확인 | 수정됨(v0.4). `scripts/test_intel.mjs` 6개 |
| M8 | 의존성에 하한만 있고 lock 파일이 없었습니다 | `requirements.txt` | 확인 | 상한을 지정했습니다. lock 파일은 아직 없습니다 |
| M9 | 한국 휴장일 달력이 없습니다 | `timing.price_available_at` | 추정 | **분석 후 유지.** 관측 달력은 `krx_calendar`로 쌓입니다. 공개 시각 영향은 교차시장 피처의 보수성 수준이라 반영하지 않았습니다(ROADMAP 0.5-B) |
| M10 | 스키마 버전이 없어 마이그레이션을 추적할 수 없었습니다 | `store.py` | 확인 | 수정됨(`PRAGMA user_version = 2`) |
| M11 | 수정한 LLM 검증이 보수적이라, 한 문장에 두 시장을 함께 언급한 정상 문장도 거부합니다 | `briefing.validate_llm` | 확인 (의도된 트레이드오프) | 유지. 이 경우 규칙 기반 문장으로 대체합니다 |

### Low

| ID | 문제 | 근거 | 상태 |
|---|---|---|---|
| L1 | Regime 카드에 출처와 기준시각이 없었습니다. ticker 값이 없을 때 대체 표시가 부적절했고, aria-label이 escape되지 않았습니다 | `intel.js` | 수정됨 |
| L2 | `.playwright/`와 `.claude/*.lock`이 gitignore에 없었습니다 | `.gitignore` | 수정됨 |
| L3 | cron 예시에 문제가 있었습니다: Debian cron은 `CRON_TZ`를 무시하고, cron 환경에 `KRX_API_KEY`가 전달되지 않고, 16:30에는 KRX 당일 값이 아직 없을 수 있습니다 | `schedule.example.cron` | 수정됨. 환경 파일 `~/.config/mi/env`를 읽는 `cron_run.sh`를 추가했습니다 |
| L4 | `write()` 출력 문구의 연산자 우선순위 버그 | `mi_cli.write` | 수정됨 |
| L5 | `vs_best_baseline` 패널이 브라우저에서 한 번도 렌더링되지 않았습니다 | `intel.js` | **확인됨(2026-10-15).** 합성 라이선스 픽스처로 학습·예측을 채운 화면에서 렌더링을 확인했습니다. 긴 CI 문구가 잘리던 문제(`td.wrap`)도 고쳤습니다 |
| L6 | scikit-learn 1.10부터 `LogisticRegression(C=…)`가 deprecated입니다 | `models.LogReg` | `scikit-learn<1.10`으로 고정해 완화했습니다. 업그레이드할 때 다시 확인해야 합니다 |
| L7 | KRX 수집이 하루당 1회씩 순차 호출하며, 호출 사이 지연이 없습니다 | `providers.krx` | 현재 한도 안에서는 문제없습니다(§3.4). 장기 백필을 할 때 보완이 필요합니다 |

### 잘 되어 있는 부분 (확인됨)

- **누출 방지:**
  - 무작위 분할이 없습니다(`shuffle`, `KFold`, `bfill`, `center=True`를 grep으로 확인).
  - fit → purge → calibration → purge → test 순서를 지킵니다.
  - 결측 대체와 스케일링은 학습 구간 안에서만 적합합니다.
  - 한국 예측은 전날 미국 종가만 씁니다(서머타임 전환 테스트 포함).
- **음성 대조:** 순수 랜덤워크에서는 어떤 모델도 VALIDATED가 되지 않습니다(`PipelineTest.test_random_walk_is_not_validated`).
- **화면 안전성:** XSS 방어(`esc`, `safeUrl`, `rel=noopener`)가 되어 있고, REAL과 DEMO가 분리되어 있습니다.
- **뉴스 저장:** 본문은 저장하지 않고, 중복 기사는 삭제하지 않고 연결만 합니다. 헤드라인은 LLM 입력에 넣지 않습니다.

---

## 3. 특별 검증 사항

### 3.1 FRED 이용조건 — 확인 (2026-10-15 원문 대조)
| 항목 | 약관 | 코드 처리 |
|---|---|---|
| 수집 | API·그래프 이용 가능, 과도한 대역폭 금지("Use an unreasonable amount of bandwidth") | 시장별 하루 1회 CSV 요청 |
| 저장·캐시 | **금지**: "Store, cache, or archive any portion …" | SQLite 저장 안 함. **`markets.json`(v0.2)은 저장함 → C1. 파생 수치 → C2** |
| ML 학습 | **금지**: "development or training of any software program … machine learning" | `ml_training='not_allowed'`, 게이트가 차단 |
| 제3자 저작권 | S&P·Nasdaq·ICE 시리즈는 데이터 소유자의 사전 허가 필요 | `redistribution='not_allowed'`, 화면에 저작권 문구 표시 |
| 호출 한도 | 수치로 정한 한도 없음 ("may impose or adjust the limit") | 해당 없음 |

### 3.2 KRX OPEN API 이용조건 — 확인 (국문 약관, 2025-12-26 시행본)
| 항목 | 약관 | 코드 처리 |
|---|---|---|
| 이용 목적 | 비상업적 목적만 가능 (제6조②) | 개인용 로컬 대시보드 |
| 제3자 제공 | 금지 (제11조②) | `data/`는 gitignore, 재배포 `not_allowed` |
| 계약 종료 후 | 받은 정보 이용 불가 (제11조③) | `conditions`에 "계약 종료 시 이용 중단(사용자 책임)" 기재 |
| 저장 | 명시 조항 없음 | `local_storage='conditional'` + 조건 3개 기재 → 저장 허용 |
| ML 학습 | 명시 조항 없음 | 사용자가 이용 신청 목적을 연구·머신러닝으로 기재해 승인받음(2026-10-15 사용자 확인) → `allowed`, 근거는 `ml_basis`에 기록 |
| 호출 한도 | **키 하나당 하루 10,000회 이하** (제8조④) | §3.4 참고 |

### 3.3 API 데이터 저장·캐싱·ML 학습 가능 여부 (현재 레지스트리 `licenses.SOURCES`)
| 소스 | 저장 | ML | 실제 상태 |
|---|---|---|---|
| FRED SP500·NASDAQ100·VIX·ICE | ✕ | ✕ | SQLite 0행. v0.2 스냅샷은 C1 |
| KRX OPEN API | 조건부(조건 기재) | ✓ (신청 목적 근거) | 키가 없어 0행 |
| Fed H.15 / H.10 / 보도자료 RSS / FOMC 일정 | ✓ | ✓ | 거시 5개 시리즈 약 32,000행 |
| SEC 보도자료 RSS | ✓ | ✓ | User-Agent를 선언하고, 초당 10회 이하로 호출 |
| GDELT DOC | ✓ (메타데이터만) | 조건부 → ✕ | 이 환경에서는 429 또는 시간 초과 |
| 사용자 라이선스 데이터 (`data/licensed/manifest.json`) | manifest 기재 내용 | manifest 기재 내용 | 없음 |

**결론:** 지금 ML 학습에 쓸 수 있는 **지수 가격** 소스가 하나도 없습니다. 그래서 12개 예측 조합이 모두 `unavailable`인 것은 정상 동작입니다.

### 3.4 KRX 수집 효율·호출 제한 — 확인
- **호출 구조 (`providers.krx`):**
  - 기준일 하루당 1회, 시장(KOSPI·KOSDAQ)별로 엔드포인트가 따로 있습니다.
  - 처음 실행할 때는 420일 중 평일 약 300일 × 2시장 = **약 600회**입니다.
  - 이후 증분 수집은 마지막 날짜에서 7일 전부터 다시 받으므로 약 6회 × 2 = **약 12회/일**입니다.
  - 둘 다 한도(10,000회/일)의 6% 이하입니다.
- **비효율 (L7):**
  - 한국 공휴일에도 호출합니다(결과는 빈 응답 → `None`으로 처리. 연 약 15회 낭비).
  - 호출 사이 지연이 없습니다.
  - 중간에 실패하면 해당 시장 전체를 실패로 처리합니다. 기존 스냅샷은 보존됩니다(`test_data` 중간 실패 테스트).
- **v0.5 위험:** 학습에는 최소 `min_train=504`와 holdout이 필요하므로 **약 10년치(약 2,500일 × 2 = 5,000회)** 백필이 필요합니다. 하루 한도 안에는 들지만, 재시작 가능한 백필 명령과 호출 간격이 필요합니다(ROADMAP 0.5-B).
- **키 처리:** curl `--config -`(stdin)로만 전달하므로 argv와 로그에 남지 않습니다. 헤더 값 주입 문자를 거부하고, 헤더가 있는 요청은 리다이렉트를 따라가지 않습니다.

### 3.5 ECOS API 키 처리·민감정보 노출 — 해당 코드 없음 / 비밀정보 스캔 통과
- **ECOS(한국은행) 연동은 구현되어 있지 않습니다.** README G절 "향후 과제"에만 언급되어 있습니다. 따라서 ECOS 키 처리 코드는 없습니다.
- **비밀정보 스캔:** 작업 트리와 **모든 git 이력**(`git rev-list --all`)에서 하드코딩된 키 패턴(`api_key|auth_key|secret|token = "…"`)은 0건입니다. `data/` 아래에서 `KRX_API_KEY=` 또는 `sk-ant-` 문자열도 0건입니다.
- **향후 주의(ROADMAP 0.6-A):** ECOS Open API는 인증키를 **URL 경로**에 넣습니다(`/api/StatisticSearch/{KEY}/json/…`). 지금의 `request_text(url)` 방식을 그대로 쓰면 키가 curl argv에 들어가고, 오류 메시지에도 URL이 나올 수 있습니다. 연동할 때는 URL을 curl config(stdin)의 `url = "…"`로 넘기고, 예외 메시지에서 키를 가려야 합니다. ECOS 약관은 **아직 확인하지 않았습니다**.

### 3.6 미국·한국 거래일과 시차 — 확인
- **세션 마감 시각:** `timing.SESSIONS`(ET 16:00, KST 15:30)에 정의되어 있습니다. 서머타임은 `zoneinfo`가 처리합니다(여름 05:00 KST, 겨울 06:00 KST).
- **결정 시각:** 마감 + 30분. 한국의 t일 예측은 미국 t−1일 종가까지만 봅니다(테스트 있음).
- **거래일 정의:** 관측된 가격 행 자체가 거래일입니다. 보간이나 forward-fill로 거래일을 채우지 않습니다.
- **남은 위험:**
  - 한국 휴장일 달력이 없습니다(M9).
  - NYSE 휴장일 달력이 없습니다. 기대 판정일은 주말만 제외한 추정값이고, 실제 판정은 h번째 관측일로 하므로 정확도에는 영향이 없습니다.

### 3.7 경제지표 관측일과 공개일 — 확인
- 모든 거시 관측값은 `macro_available_at(lag_rule)`로 공개 시각을 붙인 뒤 as-of 조인으로만 결합합니다.
  - H.15: 다음 연준 영업일 17:00 ET
  - H.10: 주간 게시 → 휴일을 반영한 화요일 16:15 ET (최대 약 1주 지연)
  - FRED 위험 지표: 다음 날 18:00 ET
- 값이 바뀌면 `observation_revisions`에 기록합니다.
- **남은 위험 (추정):** 과거 백테스트에는 **최종 수정치**를 씁니다(시점별 vintage가 아님). H.15와 H.10은 수정이 드물어 영향이 작을 것으로 봅니다. CPI·GDP처럼 수정이 잦은 지표를 추가하려면 vintage 소스가 필요합니다. 그런데 ALFRED는 FRED 약관을 그대로 받으므로 쓸 수 없습니다.

### 3.8 테스트 커버리지 — 측정함
**Python:** 2026-10-15 측정. `coverage` 7.x, 스크래치 venv, 전체 73개 테스트

| 모듈 | 커버리지 | 모듈 | 커버리지 |
|---|---|---|---|
| `mi/models.py` | 98% | `mi/briefing.py` | 91% |
| `mi/timing.py` | 98% | `mi/licenses.py` | 91% |
| `mi/store.py` | 96% | `mi/regime.py` | 88% |
| `mi/indicators.py` | 96% | `mi/macro.py` | 85% |
| `mi/forecasting.py` | 95% | `mi/events.py` | 83% |
| `mi/news.py` | 95% | `collect_data.py` | 76% |
| `mi/dataset.py` | 93% | `mi/sentiment.py` | 75% |
| `mi/validation.py` | 93% | `mi/prices.py` | 64% |
| `providers.py` | 92% | **`mi_cli.py`** | **59%** |
| **전체** | **87%** (2,128문장 중 275개 미실행) | | |

**JavaScript:**
- 단위 테스트가 없습니다(0%).
- `node --check`(구문 검사)만 합니다.
- 동작 검증은 브라우저 스모크(§3.9)가 유일합니다.
- `intel.js`의 순수 함수(`esc`, `INTEL_SHAPE`, STALE 계산)는 node 내장 `node:test`로 의존성 없이 테스트할 수 있습니다(ROADMAP 0.4-E).

### 3.9 실제 브라우저 환경 UI 동작 — 확인 (범위 제한 있음)
- `scripts/ui_smoke.py`는 headless Chromium 1247로 실제 렌더링을 검사합니다.
  - 조합: REAL·DEMO × 6탭 × 2시장 × 3기간 × 데스크톱·390px
  - 검사 항목: 콘솔 오류, 가로 넘침, 빈 화면
- 2026-10-15 실데이터에서 **24 views OK**였습니다. 모든 패널을 채운 합성 픽스처(`MI_DATA_DIR`)에서도 통과했습니다.
- **검증하지 못한 것:**
  - Firefox·Safari 등 다른 브라우저
  - 키보드 접근성
  - 실제 확률·성능이 채워진 상태의 `vs_best_baseline` 패널(L5)
  - 네트워크 지연이나 부분 실패 중의 화면 전환

### 3.10 보안 점검 — 확인 (2026-10-15)

**점검 범위:** 비밀정보, 외부 요청, 하위 프로세스, 입력 파싱, 화면 XSS, 로컬 서버, 파일 권한, 의존성 취약점, 공개 저장소 이력, LLM 경로

| ID | 항목 | 결과 | 조치 |
|---|---|---|---|
| S1 | 공개 저장소 이력 (`aquariusmin/market-intelligence-dashboard`, PUBLIC) | 커밋된 파일 33개 전부 소스·문서입니다. `data/`, `.env`, SQLite, `markets.json`은 이력에 한 번도 없습니다. 키 패턴 0건 | 없음 |
| S2 | 의존성 취약점 | `pip-audit`로 `.venv` 고정 버전 15개 확인 → 알려진 취약점 없음 | 없음 |
| S3 | 외부 요청의 프로토콜 | 모든 공급자 URL이 HTTPS입니다. 다만 인증 헤더가 없는 요청은 `--location`으로 리다이렉트를 따라가므로, 이론상 http나 다른 프로토콜로 내려갈 수 있었습니다 | **수정:** curl `--proto =https --proto-redir =https` (`providers._request_text_once`, 테스트 추가) |
| S4 | 로컬 개발 서버 | `python3 -m http.server`가 저장소 전체(`.git`, `.venv`, `data/mi.sqlite`, `data/cron.log`)를 제공했고 Host 헤더를 검사하지 않았습니다. 외부 웹페이지가 DNS 리바인딩으로 로컬 데이터를 읽을 수 있는 구조였습니다(추정 위험, 127.0.0.1 바인딩으로 직접 접근은 차단됨) | **수정:** 새 `scripts/serve.py`(허용 파일 5종, Host 검사 시 421, CSP·nosniff·no-referrer). `make dev`와 `ui_smoke.py`가 이 서버를 쓰므로 CSP 위반은 스모크 테스트에서 실패합니다. 실제 요청으로 확인: `/data/mi.sqlite`·`/.git/config` 404, 리바인딩 Host 421 |
| S5 | 파일 권한 | JSON 출력과 `markets.json`은 0600이었지만 `mi.sqlite`는 0644였습니다(다른 로컬 사용자가 읽기 가능) | **수정:** `store._owner_only`가 DB·`-wal`·`-shm`을 0600으로 맞춥니다. `cron_run.sh`는 `umask 077`. 실데이터 DB는 다음 실행 때 0600이 됩니다 |
| S6 | cron 환경 파일 | `cron_run.sh`가 `~/.config/mi/env`를 셸 코드로 `source`하는데, 권한을 확인하지 않았습니다 | **수정:** 본인 소유가 아니거나 그룹·기타 쓰기 권한이 있으면 실행을 거부합니다(권한 8종으로 확인) |
| S7 | 화면 XSS | 외부 문자열(뉴스 제목·출처·오류 메시지·일정)은 모두 `esc()`를 거칩니다. 링크는 `safeUrl`(http/https만) + `noopener noreferrer`입니다. 유일하게 escape되지 않은 곳은 `data-perf-model` 속성 값이었습니다(값은 파이프라인이 만든 모델 이름이라 실제 위험은 낮음) | **수정:** `esc(n)` |
| S8 | 하위 프로세스·역직렬화 | `shell=True`, `eval`, `pickle`, `yaml.load` 없음. curl 인자는 리스트로 넘기고, 헤더 값의 줄바꿈·따옴표를 거부합니다 | 없음 |
| S9 | XML 파싱 (RSS) | `xml.etree` + expat 2.7.4(엔티티 확장 공격 방어 내장). 응답은 10MB로 제한(`--max-filesize`) | 없음 |
| S10 | LLM 경로 | 키는 SDK가 환경변수에서 읽습니다. 예외 메시지는 200자로 자르며, 키를 담지 않습니다. 언론사 제목은 입력하지 않습니다. 출력은 `validate_llm`을 통과해야 합니다 | C2 수정으로 FRED 파생 수치도 입력에서 제외 |
| S12 | 토스증권 API 토큰 (2026-10-16 추가) | 토큰에 권한 범위가 없어 주문까지 가능 | `mi/toss.py`: 시세 경로 허용 목록(주문·계좌는 요청 전 거부, 테스트), 계좌 헤더 미전송, 키·토큰은 curl stdin, HTTPS 전용, 허용 IP 등록 필수 |
| S11 | Claude Code 로컬 설정 | `.claude/settings.local.json`(개인 플러그인 설정)이 gitignore에 없었습니다 | **수정:** gitignore 추가 |

**남은 위험(수용):**
- CSP가 `'unsafe-inline'`을 허용합니다. v0.2 `index.html`의 인라인 DEMO 스크립트를 유지해야 하기 때문입니다(원칙 6). 외부 출처 연결은 `connect-src 'self'`로 막혀 있습니다.
- `python3 -m http.server`를 직접 쓰는 것은 막을 수 없습니다. README에 쓰지 말라고 명시했습니다.
- 디스크는 암호화하지 않습니다(WSL 홈 디렉터리 권한에 의존).

---

## 4. 결정 (2026-10-15, 사용자 위임으로 적용)

| ID | 쟁점 | 선택지 | 적용한 결정 |
|---|---|---|---|
| D1 | C1: v0.2 `markets.json`의 FRED 원시 시세 | (a) 유지(현 상태, 약관 충돌 위험 감수) (b) 가져오기만 하고 저장하지 않는 표시 전용으로 전환(v0.2 동작 변경) (c) 미국 지수에 라이선스 데이터를 확보한 뒤 교체 | **(a) 유지 + 문서화**(README B절). 중기 (c) |
| D2 | C2: `briefings`에 저장된 FRED 파생 수치 | (a) 유지 (b) 이후 저장분부터 FRED 시장 문장은 수치를 빼고 국면 라벨만 저장 (c) 기존 행 삭제(사용자 데이터 삭제 → 승인 필요) | **(b) 적용.** LLM 입력도 같은 사본을 씁니다. 기존 행은 보존합니다 |
| D3 | 감성 방법 이름 | `lexicon-en-v1` 유지 / `lexicon-en-v2`로 변경 | **`lexicon-en-v2` 적용** |
| D4 | KRX ML 이용 | KRX에 서면 문의 / 문의하지 않음 | **해소.** 사용자가 이용 신청 시 목적을 연구·머신러닝으로 기재해 승인받았음을 확인(2026-10-15). 레지스트리를 `allowed`로 바꾸고 근거를 기록 |
| D5 | 미커밋 작업 | 커밋 / 보류 | **로컬 커밋**(push 없음) |

## 5. 검증 실행 기록 (2026-10-15, 이 환경에서 실제로 실행)

```text
cd scripts && python3 test_data.py                                     → 12 tests OK
cd scripts && ../.venv/bin/python -m unittest test_mi_forecasting test_mi_analysis test_mi_cli → 65 tests OK
node --check real-data.js && node --check intel.js                     → OK
ui_smoke.py (실데이터, serve.py의 CSP 적용)                             → checked 24 views, OK
uvx pip-audit (.venv 고정 버전 15개)                                     → No known vulnerabilities found
coverage (스크래치 venv, 보안 수정 전 측정)                             → 87%
audit_data.py                                                           → FAIL 0, WARN 5 (주로 KRX 키 없음)
```

**실행하지 않은 것:**
- `briefing --llm` 실제 호출 (유료 → 승인 필요)
- KRX 실제 수집 (키 없음)
- pyright (도구 없음)
