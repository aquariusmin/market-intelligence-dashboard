# Market Intelligence Dashboard v1.0 (local)

미국·한국 4개 지수(S&P 500, NASDAQ 100, KOSPI, KOSDAQ)의 시장 국면·위험·거시지표·뉴스·한국어 브리핑을 매일 확인하고, **라이선스와 시간순 검증을 통과한 경우에만** 1/5/20거래일 방향 확률을 표시하는 개인용 로컬 대시보드입니다. 자동매매·주문·계좌 기능은 없으며 투자 권유가 아닙니다. 외부 배포나 abovemin.com 통합은 하지 않았습니다.

기존 v0.2 화면(HTML/CSS/Vanilla JS)과 DEMO 모드를 그대로 유지하고, 그 위에 Python 분석 엔진(`scripts/mi/`)과 v1.0 화면 레이어(`intel.js`)를 추가했습니다.

> **현재 상태 요약 (2026-10-16 실측)**
> - **데이터:**
>   - KOSPI·KOSDAQ: KRX 공식 2016-01-04부터 + 토스증권 당일 종가(잠정치, 다음 날 KRX로 확정·대조)
>   - S&P 500·NASDAQ 100: 화면 표시는 FRED. 예측 학습에는 토스증권 SPY·QQQ ETF 가격을 씁니다
>   - Fed H.15/H.10 거시지표, Fed·SEC 공식 발표 뉴스, FOMC 일정
> - **방향 예측 검증 (12개 조합, 2026-10-15 사전 고정 기준):**
>   - 통과 2개: **NASDAQ 100 1거래일**(QQQ, LightGBM, 클래스 빈도 대비 log loss 개선 90% 하한 +0.015), **KOSDAQ 5거래일**(LightGBM, 하한 +0.0003으로 아슬아슬)
>   - 나머지 10개는 NOT VALIDATED입니다.
>   - 12개를 시험해 2개가 통과한 것이라 우연일 가능성이 있습니다. 실시간 판정이 30건 이상 쌓일 때까지 참고용으로만 보세요. 거래 비용은 아직 반영하지 않았습니다.
> - **자동 갱신:** cron 08:30·16:30(평일)과 일요일 재학습. 16:30 실행은 토스 당일 종가로 한국 예측을 만듭니다.
---

## E. Local Setup Guide

필요: Python 3.10+, `curl`, (권장) [uv](https://docs.astral.sh/uv/). DB 서버·클라우드는 필요 없습니다. `make`는 선택 사항이며 각 타깃은 아래 명령 하나와 같습니다.

```sh
# 1) 설치 (.venv + numpy/pandas/scikit-learn/lightgbm/scipy)
make setup                      # = uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt

# 2) 데이터 수집: v0.2 가격 수집기 + Fed 거시지표 (KRX 키 설정은 아래 "KRX API 키" 참고)
make ingest                     # = .venv/bin/python scripts/mi_cli.py ingest

# 3) 분석·학습·예측·뉴스·브리핑 (전체: make all)
make regime                     # 국면·위험·거시 → data/dashboard/regime.json, macro.json
make train                      # 라이선스 게이트 + walk-forward 검증 → performance.json
make forecast                   # 미판정 예측 결과 판정 + 실시간 예측 추가 → forecast.json
make news                       # Fed/SEC RSS + GDELT 메타데이터 → news.json
make briefing                   # 규칙 기반 한국어 브리핑 → briefing.json
make status                     # 시스템 상태(+ SQLite 무결성·행 수·백업 수) → status.json
.venv/bin/python scripts/mi_cli.py backup   # data/mi.sqlite 온라인 백업 → data/backups/ (자동 삭제 없음)
.venv/bin/python scripts/mi_cli.py backfill # KRX KOSPI/KOSDAQ 10년 이력 → SQLite (키 필요 · 재개 가능 · 하루 8,000회 이내)

# 4) 대시보드
make dev                        # = python3 scripts/serve.py --port 3000 (127.0.0.1 전용 · 허용 파일만 · Host 검사 · CSP)
```

[http://localhost:3000](http://localhost:3000) 접속 후, 명령 실행 뒤에는 화면의 **JSON 다시 읽기**를 누르세요(로컬 파일만 읽음). FastAPI 백엔드는 도입하지 않았습니다. 모든 결과가 정적 JSON이므로 `localhost:8000` 서버는 필요 없습니다.

복합 작업: `make morning` / `make evening` (ingest→regime→forecast→news→briefing), `make weekly` (ingest→regime→train→forecast), `make all`. 한 단계가 실패해도 나머지 단계와 마지막 status 갱신은 계속 실행되고, 실패한 단계가 있으면 종료 코드 1과 traceback을 남깁니다. 브리핑은 실패한 단계나 3일 넘게 지난 입력을 `input_warnings`로 표시합니다.

선택 옵션:
- `mi_cli.py ingest --with-fred-risk`: FRED에 게시된 VIX·ICE 신용스프레드를 **표시 전용**으로 가져옵니다(SQLite 보관 안 함). FRED/ICE 약관이 저장·복제를 제한하므로 기본은 꺼져 있고, 켜는 판단은 사용자 몫입니다. 표시용 `fred_risk.json`은 3일이 지나면 무시·삭제됩니다.
- `mi_cli.py briefing --llm`: `ANTHROPIC_API_KEY`가 있을 때 요약 문단만 Claude(`claude-opus-5-5`, effort low, 서버측 refusal fallback 사용)로 다듬습니다. **유료 호출**이므로 명시적으로 플래그를 줄 때만 실행되며, 입력에 없는 수치가 나오면 결과를 버리고 규칙 기반 문장을 유지합니다. `pip install anthropic` 필요.
- `MI_FINBERT=1`: `transformers`가 설치돼 있으면 영문 제목 감성에 ProsusAI/finbert(CC BY-NC-SA 4.0, 비상업용)를 사용합니다. 기본 설치에는 포함하지 않았습니다.
- `MI_DATA_DIR=/path`: 데이터 디렉터리 변경(테스트·격리용). 가격 수집기 출력(`markets.json`)도 이 디렉터리에 씁니다.
- 선택 의존성: `make setup-optional`(= `requirements-optional.txt`: `briefing --llm`용 `anthropic`, UI 스모크용 `playwright`).

### KRX API 키

키는 **저장소 밖의 파일 하나**에만 둡니다. 코드·JSON·`data/`·커밋에는 넣지 않습니다.

```sh
mkdir -p ~/.config/mi && chmod 700 ~/.config/mi
printf 'export KRX_API_KEY=%s\n' '여기에_발급받은_키' > ~/.config/mi/env   # 직접 터미널에서 입력
chmod 600 ~/.config/mi/env
```

- `scripts/mi_cli.py`(수동 실행)와 `scripts/cron_run.sh`(cron)가 이 파일을 자동으로 읽습니다. 다른 경로를 쓰려면 `MI_ENV_FILE=<경로>`를 지정합니다.
- 파일이 본인 소유가 아니거나 권한이 600이 아니면 읽지 않습니다. 이때 수동 실행은 경고를 출력하고, cron 실행은 실행을 거부합니다.
- `mi_cli.py`는 `KRX_API_KEY`, `ANTHROPIC_API_KEY` 두 줄만 읽고 셸 코드로 실행하지 않습니다. 이미 `export`한 값이 있으면 그 값이 우선합니다.
- v0.2 수집기를 직접 실행할 때(`python3 scripts/collect_data.py`)는 같은 터미널에서 `export KRX_API_KEY=...`를 하거나 `set -a; . ~/.config/mi/env; set +a`로 불러옵니다.
- 확인: `python3 scripts/collect_data.py --check-krx 2026-10-14`

### 토스증권 Open API (당일 종가 · 미국 ETF)

KRX는 그날 종가를 다음 날 아침에 게시합니다. 토스증권 Open API를 쓰면 장 마감(15:30) 15분 뒤부터 당일 종가를 받을 수 있습니다.

```sh
# WTS 설정 > Open API에서 client_id·client_secret 발급 + 허용 IP(집 공인 IP) 등록 후
printf 'export TOSS_CLIENT_ID=%s\nexport TOSS_CLIENT_SECRET=%s\n' '<id>' '<secret>' >> ~/.config/mi/env
chmod 600 ~/.config/mi/env
```

- **동작:** `ingest`(아침·저녁 자동 실행 포함) 때 함께 받습니다.
  - KOSPI·KOSDAQ: 최근 2주 일봉을 받아, KRX에 아직 없는 날짜를 **잠정치**로 씁니다. 다음 날 KRX 공식값이 들어오면 두 값을 대조하고, 다르면 `observation_revisions`(kind=`crosscheck`)에 기록합니다. 어느 값도 덮어쓰지 않습니다.
  - SPY·QQQ: 2016년부터 일봉을 받습니다. 처음에만 시장당 약 13회 호출하고, 이후에는 최근분만 받습니다.
- **미국 예측:** 지수 데이터(FRED)는 ML 학습이 금지되어 있어서, **S&P 500은 SPY, NASDAQ 100은 QQQ 가격**으로 학습하고 판정합니다. 화면의 지수 값은 그대로 FRED이고, 예측 카드에 "추종 ETF 기준"이라고 표시합니다. ETF는 배당·보수만큼 지수와 다릅니다.
- **장중 가격 배제:** 장 마감 15분 뒤 이전의 일봉(진행 중인 세션)은 종가로 쓰지 않습니다.
- **보안:** 토스 토큰에는 권한 범위가 없어 주문도 가능합니다. `mi/toss.py`는 시세 경로만 허용 목록으로 호출하고(주문·계좌 경로는 요청 전에 거부), 계좌 헤더를 보내지 않으며, 키·토큰은 curl stdin으로만 전달합니다. 집 공인 IP가 바뀌면 403이 나므로 허용 IP를 다시 등록하세요.
- **이용 조건 (FAQ 원문):** "투자자 본인의 매매 목적에 한하여 이용", 상업적 이용과 제3자 배포 금지. 받은 데이터는 `data/`에만 저장하고 공개 저장소에 올리지 않습니다. 개인 매매 판단용 모델 학습은 목적 범위 안으로 해석했습니다(`licenses.SOURCES['toss_openapi'|'toss_etf']`).
- **당일 예측:** 평일 16:30 `evening` 실행이 당일 종가로 한국 예측을 만듭니다. 다음 거래일 개장 전이므로 실시간 성과 집계 대상입니다.

### KRX 장기 이력 백필 (예측 학습용)

학습에는 최소 504거래일과 holdout 구간이 필요하므로, 키를 넣은 뒤 한 번 실행합니다.

```sh
.venv/bin/python scripts/mi_cli.py backfill                      # 기본: 10년 전 1월 2일부터 어제(KST)까지
.venv/bin/python scripts/mi_cli.py backfill --since 2016-01-04 --max-calls 500   # 범위·호출 수 지정
.venv/bin/python scripts/mi_cli.py train && .venv/bin/python scripts/mi_cli.py forecast
```

- 하루 1회 호출 = 시장 1개 × 기준일 1개입니다. 10년 × 2시장이면 약 5,000회이고, KRX 한도는 키당 하루 10,000회입니다.
- 이 도구는 하루 8,000회까지만 쓰고(`provider_calls`에 실행 간 누적), 호출 사이에 0.2초 쉽니다. 한도에 닿으면 멈추며, 다음 날 같은 명령으로 이어서 받습니다.
- 이미 받은 날짜와, 일주일 이상 지난 "데이터 없음" 날짜(휴장일)는 다시 묻지 않습니다. 응답은 `krx_calendar`에 남아 관측된 거래일 달력이 됩니다.
- 일시적 오류(JSON이 아닌 응답, 서버 5xx, 시간 초과)는 5초, 30초 쉬고 다시 시도합니다. 인증 오류나 이후에도 계속되는 오류가 나면 멈추고, 그때까지 받은 행은 보존합니다. 결과는 `data/dashboard/backfill.json`에 기록됩니다.
- v0.2 `markets.json`은 바꾸지 않습니다. 분석과 학습은 SQLite 아카이브와 스냅샷을 합쳐 읽습니다.

### 뉴스 분류·감성 정확도 평가 (직접 라벨)

```sh
.venv/bin/python scripts/mi_cli.py label-export   # data/eval/news_labels.csv에 새 제목 추가 (기존 라벨은 보존)
# 파일의 label_category(뉴스 범주 이름 또는 Other)와 label_sentiment(positive/neutral/negative)를 채운 뒤
.venv/bin/python scripts/mi_cli.py label-score    # 정확도·macro-F1 → data/dashboard/nlp_eval.json, 뉴스 탭에 표시
```

라벨이 100개 이상이고 macro-F1이 0.5 이상일 때만 "평가 통과"로 표시하고, 그 전에는 "참고용"입니다(2026-10-15 사전 고정).

### 라이선스가 확인된 가격 데이터로 예측 활성화

ML 학습 권한을 보유한 데이터(예: 계약한 유료 공급원)가 있으면 `data/licensed/<market>.csv`(`date,close` 헤더)와 `data/licensed/manifest.json`을 만드세요. 시장 코드는 `sp nq ks kq`입니다.

```json
{"markets": {"sp": {"file": "sp.csv", "provider": "공급자명", "dataset": "S&P 500 daily close",
  "terms_url": "https://…계약/약관 URL", "ml_training": "allowed", "local_storage": "allowed",
  "redistribution": "not_allowed", "verified_on": "YYYY-MM-DD", "publication_delay_minutes": 15}}}
```

`publication_delay_minutes`는 공급자가 일별 종가를 장 마감 후 몇 분 뒤 제공하는지입니다. 적지 않으면 종가를 다음 영업일 09:00(거래소 현지 시각)에야 알 수 있다고 보수적으로 가정합니다.

필수 항목이 비거나 `ml_training`이 `allowed`가 아니면 학습을 거부합니다. 이 파일은 사용자가 보유한 권한을 기록하는 곳이며, 프로그램이 권한을 만들어내거나 가정하지 않습니다. 이후 `make train && make forecast`를 실행하세요.

### 자동 갱신 (선택)

`scripts/schedule.example.cron`에 KST 기준 예시가 있습니다. `CRON_TZ`는 cronie 계열에서만 동작하며, Debian/Ubuntu(WSL 포함) cron은 무시하므로 시스템 시간대를 Asia/Seoul로 두거나 시각을 환산하세요. 각 작업은 `scripts/cron_run.sh`로 실행되며, 이 래퍼가 `~/.config/mi/env`(권한 600, `export KRX_API_KEY=...`)를 읽고 `flock`으로 작업 중복 실행을 막습니다.

| KST | 작업 |
| --- | --- |
| 08:30 (월–토) | `morning`(ingest 포함): 아침 브리핑. 미국 장 마감은 서머타임 기준 05:00 KST, 표준시 06:00 KST입니다. |
| 16:30 (월–금) | `evening`: 한국장 마감 분석 (KRX 키 필요). KRX 일별 지수가 다음 날 아침에 게시되면 08:30 실행에서 반영됩니다. |
| 일 10:00 | `weekly`: 재검증·재학습 |

- **로컬 컴퓨터가 꺼져 있거나 잠자기 상태면 실행되지 않으며, 놓친 실행을 나중에 재실행하지 않습니다.**
- 휴장일·게시 지연이 있으면 새 관측값이 없을 뿐입니다. 같은 날 다시 실행해도 같은 모델 버전의 예측과 뉴스는 중복 저장되지 않습니다. 브리핑과 백테스트 실행 기록은 실행마다 추가됩니다(이력). 재학습으로 모델 버전이 바뀌어 같은 기준일 예측이 한 행 더 생기면, 표에는 남기되 실시간 성과 집계에서는 처음 기록만 셉니다.
- **실시간 예측의 시간 규칙:** 마지막 종가가 7일보다 오래되면 예측을 만들지 않고 "입력 데이터가 오래됨"으로 표시합니다. 다음 거래일 개장(미국 09:30 ET, 한국 09:00 KST) 이후에 기록된 예측은 결과를 이미 알 수 있었던 기록이므로, 표에는 "늦은 기록 · 집계 제외"로 남기고 실시간 성과에는 넣지 않습니다.
- 서머타임은 `zoneinfo`로 처리합니다.

### 보안 기본값

- **키:** `KRX_API_KEY`·`ANTHROPIC_API_KEY`는 환경변수로만 읽습니다. KRX 키는 curl 설정(stdin)으로만 전달되어 명령줄·로그·오류 메시지에 남지 않습니다. cron 환경 파일은 본인 소유이고 그룹·기타 쓰기 권한이 없을 때만 읽습니다(아니면 실행 거부).
- **네트워크:** 모든 외부 요청은 HTTPS만 허용합니다(리다이렉트 포함, `--proto =https`). 인증 헤더가 있는 요청은 리다이렉트를 따라가지 않습니다.
- **로컬 서버:** `scripts/serve.py`는 127.0.0.1에만 바인딩합니다. 대시보드 파일(`index.html`, JS 2개, `data/markets.json`, `data/dashboard/*.json`)만 제공하고 `.git`·`.venv`·`data/mi.sqlite`·로그는 404로 막습니다. Host가 localhost/127.0.0.1이 아니면 421을 반환해 DNS 리바인딩을 막고, 외부 출처를 허용하지 않는 CSP를 보냅니다. `python3 -m http.server`는 저장소 전체를 노출하므로 쓰지 마세요.
- **로컬 파일:** JSON 출력과 SQLite(`-wal`/`-shm` 포함)는 소유자 전용(0600)입니다. cron 실행은 `umask 077`입니다.
- **화면:** 외부에서 온 문자열은 모두 `esc()`로 이스케이프합니다. 링크는 http(s)만 허용하고 `noopener noreferrer`를 붙입니다.
- **LLM(옵트인):** 뉴스 제목은 보내지 않습니다. 보관이 허용되지 않은 소스(FRED)의 수치도 보내지 않습니다.

---

## A. Implementation Summary

| 단계 | 구현됨 | 미완료 / 제한 |
| --- | --- | --- |
| v0.4 Regime & Risk | 5개 국면 규칙 분류, 3일 확인 규칙, 국면 이력·전환, 단기·중기 추세, 0–100 위험 점수와 구성요소, 상승·위험 요인 문장, 변동성 추이 | HMM 미도입. FRED 가격에 통계 모델을 적합하는 것도 ML 금지 조항에 걸릴 수 있어 보류했습니다. VIX·신용스프레드는 옵트인. KOSPI/KOSDAQ는 KRX 키 필요 |
| v0.5 ML Forecasting | 시점 정합 피처(기술 13 + 거시 + 교차시장), 변동성 비례 3분류 라벨, 4개 모델(클래스 빈도·모멘텀 규칙·로지스틱·LightGBM), 라이선스 게이트, 입력별 라이선스 필터 | **실데이터 예측 UNAVAILABLE** (허용 데이터 없음). Ensemble/RF 미도입 |
| v0.6 Validation | purge/embargo walk-forward(expanding·rolling), 개발/holdout 분리, 4종 보정(none/temperature/sigmoid/isotonic), 전체 지표, 블록 부트스트랩, 사전 고정 검증 기준, 예측 이력 추가 전용 저장·판정, 백테스트/실시간 분리 | 실데이터 성능 없음. 합성 데이터 테스트로만 검증 |
| v0.7 News | Fed·SEC RSS, GDELT DOC API, URL·제목 유사도 중복 제거, 9개 이벤트 분류, 중요도, 영문 키워드 감성(+FinBERT 선택), 메타데이터만 저장 | GDELT는 이 환경에서 HTTP 429/시간 초과. 네이버(저장 금지 약관)·DART·한국은행 발표 미연동. 한국어 감성은 검증된 모델이 없어 미산출. 뉴스는 예측 모델과 분리 |
| v0.8 Briefing | 8개 섹션 한국어 규칙 기반 브리핑, 출처 목록, 전일 대비 변화, FOMC 일정, LLM 요약(옵트인·수치 검증) | LLM 경로는 모의 테스트만 함(유료 호출 미실행). 한국 경제 일정 소스 없음 (`data/events.local.json`으로 수동 추가 가능) |
| v0.9 Integration | SQLite 아카이브(가격·거시·예측·백테스트·뉴스·브리핑·작업 로그, 개정 이력), CLI, Makefile, cron 예시, 작업 실패 격리 | 스케줄러는 cron 예시만 제공 |
| v1.0 Dashboard | 6개 탭(System Status 추가), 국면 스트립, 차트 기간 선택(1M~ALL), 1D/5D/20D·시장 전환, 상태 배지, 모든 패널 출처·기준시각, DEMO 유지 | 차트 확대(줌)는 기간 선택으로 대체 |

## B. Architecture

```text
FRED (v0.2, SP500/NASDAQ100) ─┐                 Fed H.15/H.10 (DDP) ─┐   Fed/SEC RSS · GDELT · FOMC
KRX OPEN API (키 필요) ───────┤                                       │            │
  scripts/collect_data.py ──> data/markets.json (v0.2 스냅샷, 기존 그대로)│            │
                                │  KRX만 보관 ↓                        ↓            ↓
                       data/mi.sqlite  (prices · macro · forecasts · backtest_* · news · briefings · job_runs)
                                │
     mi/indicators ─ mi/regime ─┼─ mi/dataset (as-of 정렬·라이선스 필터) ─ mi/validation ─ mi/forecasting
                                │                                    mi/news · mi/events · mi/briefing
                                ↓
              data/dashboard/*.json  (regime · macro · performance · forecast · news · briefing · status)
                                ↓
          index.html (v0.2 UI + DEMO) ← real-data.js (v0.2 REAL) ← intel.js (v1.0 패널)
```

- `scripts/mi_cli.py`: 파이프라인 진입점. 단계별 실패를 격리하고 `job_runs`에 기록합니다. 결과 JSON은 원자적으로 교체됩니다.
- SQLite 스키마는 표준 SQL 타입만 사용하므로 PostgreSQL로 옮길 수 있습니다. 값이 바뀌면 `observation_revisions`에 이력을 남기고, 행을 삭제하지 않습니다.
- FRED 시세는 약관상 저장·보관이 금지되어 SQLite에 복사하지 않습니다. v0.2 스냅샷만 읽습니다.
- 브리핑은 화면용과 보관용을 따로 만듭니다. `briefings` 테이블과 옵트인 LLM 입력에는 `storage_allowed()`를 통과하지 못한 소스(FRED)의 가격 파생 수치(수익률·변동성·낙폭·위험 점수)를 넣지 않고 국면·위험 등급 라벨만 남깁니다(`redacted_markets`). 2026-10-15 이전에 저장된 2건은 이력 보존 원칙에 따라 그대로 둡니다.
- **알려진 라이선스 위험:** v0.2 `data/markets.json`은 기존 동작대로 FRED 원시 종가(약 800일)를 디스크에 보관합니다. FRED 약관의 저장·캐시 금지 조항과 충돌할 소지가 있습니다. 미국 지수의 라이선스 데이터를 확보하면 교체할 예정입니다(`CODE_REVIEW.md` C1, `ROADMAP.md`).

## C. Model Methodology

**예측 대상.** \(R_{t,h}=P_{t+h}/P_t-1\), h ∈ {1, 5, 20} 관측 거래일.

- 라벨: \(|R| \le k\,\sigma_{60,t}\sqrt{h}\)이면 Neutral, 그보다 크면 Bullish, 작으면 Bearish.
- \(\sigma_{60,t}\)는 t까지의 60일 일간 로그수익률 표준편차입니다.
- k = 0.43은 정규분포에서 약 1/3이 Neutral이 되도록 **사전에 고정한 값**이며, 학습·검증 결과로 조정하지 않습니다.
- 임계값은 예측과 함께 저장되어 실시간 판정에 그대로 쓰입니다.

**시점 정합성.** 모든 관측값에 `available_at`(UTC), 모든 예측에 `decision_time`(장 마감 +30분)을 부여하고 `available_at ≤ decision_time`인 값만 as-of 조인합니다.

- 한국 예측(16:00 KST)은 전일 미국 종가까지만 봅니다. 당일 미국 종가는 아직 없습니다. 미국 예측은 당일 한국 종가를 봅니다.
- H.15는 익영업일 17:00 ET, H.10은 주간 게시(다음 주 화요일 16:15 ET)로 보수적으로 지연시켰습니다. 영업일은 미국 연방 공휴일(Fed 휴일)을 반영합니다.
- 교차시장 가격은 소스별 게시 시각을 씁니다. KRX OPEN API는 다음 평일 08:00 KST, 사용자 라이선스 데이터는 manifest의 `publication_delay_minutes`(없으면 다음 영업일 09:00)입니다.
- 같은 시각에 게시된 관측값(예: H.10 한 주치)은 가장 최근 관측일 값을 씁니다.
- 서머타임 전환 구간 테스트를 포함합니다.

**피처.**
- 수익률 1/5/20/60일, 20/50/200일선 이격, RSI14, MACD 히스토그램/가격, 20/60일 실현변동성과 비율, 252일 낙폭.
- 미국 10년물 수준·20일 변화, 2년·3개월 20일 변화, 10–2년 스프레드, USD/KRW 5/20일 변화, 광의 달러지수 20일 변화.
- 타 시장 1/5일 수익률, 20일 모멘텀, 상대강도, 60일 상관.
- **ML 학습 권한이 `allowed`인 소스의 피처만** 포함합니다. 제외된 피처는 `dropped_features`에 기록됩니다.
- 결측 대체와 스케일링은 각 fold의 학습 구간 안에서만 적합합니다.

**모델.**
- 과거 클래스 빈도, 모멘텀 규칙(20일 수익률 부호별 학습구간 빈도), 다항 로지스틱 회귀(C ∈ {0.01, 0.1, 1}), LightGBM(얕은 트리 3개 설정).
- 딥러닝은 도입하지 않았습니다.

**검증.**
1. 평가 가능 구간을 시간순으로 **개발(40%) → purge 간격 → holdout(60%)**으로 나눕니다. holdout 시작일은 시장·기간별 첫 실행 때 `holdout_registry`에 고정되고 이후 재학습에서도 다시 정하지 않습니다. 매 실행에는 직전 판정(`previous_run`)이 함께 기록됩니다.
2. 개발 구간의 walk-forward에서 하이퍼파라미터·보정 방법·주 모델을 log loss로 선택합니다.
3. holdout 결과만 보고합니다.
4. 각 fold(21일마다 재학습, 확장 창 기본·rolling 선택)는 **fit → purge → 보정(마지막 25%) → purge → test** 순서입니다.
5. 표본 i는 결과 관측 위치 i+h+embargo가 다음 구간 시작보다 앞설 때만 사용해, 겹치는 5D/20D 구간을 제거합니다.

**지표.** Accuracy, Balanced Accuracy, Macro F1, 혼동행렬, 다중클래스 Brier, Log Loss, 클래스별 신뢰도 곡선과 ECE(가장 나쁜 클래스의 값), 63일 rolling accuracy, 기준 모델 대비 Brier skill, log loss·Brier 개선의 이동 블록 부트스트랩 90% CI(블록 = max(2h, n^(1/3), 10)).

**검증 통과 기준(사전 고정).** 아래를 모두 만족해야 `VALIDATED`입니다. 그렇지 않으면 확률을 흐리게 표시하고 "신뢰할 수 있는 예측이 아님"과 사유를 함께 보여줍니다.
- 유효 holdout 표본 ≥ 50: 비중첩 표본(n/h)을 손실 차이의 1차 자기상관으로 한 번 더 줄인 값
- class_frequency **와 가장 강한 비교 모델** 각각에 대해 log loss 개선 CI 하한 > 0, Brier 개선 CI 하한 > 0. 가장 강한 비교 모델은 holdout log loss 기준이며, 후보는 기준 모델(class_frequency, momentum_rule)이고 LightGBM에는 로지스틱 회귀도 포함됩니다.
- **Balanced accuracy가 기준 모델보다 2%p 이상 높음** (확률만 날카로워지고 방향 정보가 없는 모델 배제)
- ECE ≤ 0.08 (클래스별 ECE 중 최댓값)
- holdout log loss가 개발 구간보다 10% 넘게 나쁘지 않음 (과적합 신호)
- 이 기준(`validation.GATE_FIELDS`)은 실행 중에 덮어쓸 수 없습니다. 지금은 실데이터 학습 결과가 없는 상태에서 강화한 것입니다(2026-10-15).

**평가 성능.** 실데이터 결과는 **없습니다**(라이선스 게이트).

합성 데이터(테스트 전용)에서 확인한 내용:
- 순수 랜덤워크 6개 조합(2개 시장 × 3기간)은 모두 `not_validated`였습니다. 거짓 검증은 없었습니다.
- 학습 가능한 평균회귀 신호가 있는 경우, 로지스틱·LightGBM이 기준 모델 대비 log loss 개선 CI 하한 ≈ 0.09로 검증을 통과했습니다.
- 합성 결과는 `data/`에 저장되지 않으며 화면에 실제 성능으로 표시되지 않습니다.

### 전망 변화 설명 (ROADMAP v0.6)

- 검증을 통과한 모델에만 만듭니다(현재 KOSDAQ 5거래일). 신뢰할 수 없는 확률에 그럴듯한 설명을 붙이지 않기 위해서입니다.
- 같은 모델로 직전 거래일 입력과 오늘 입력을 각각 계산하고, 입력을 하나씩 직전 값으로 되돌려 확률 변화를 입력별로 나눕니다(`mi/explain.py`, LLM 없음). 0.5%p 미만의 영향은 표시하지 않고, 입력 간 상호작용 몫은 따로 기록합니다.
- 예측 카드와 브리핑 "Outlook Changes"에 표시합니다. 모델 입력의 변화를 보여 줄 뿐, 시장 움직임의 원인을 뜻하지 않습니다.
- 브리핑의 "전일 대비 확률 변화"는 기준일 차이가 7일 이내인 예측끼리만 비교합니다.

### 전일 대비 변화 (ROADMAP v0.4 Daily Market Intelligence)

- `mi/daily.py`가 시장마다 현재 상태와 **전 거래일 상태**를 비교합니다. 전 거래일 상태(`regime.analyze`의 `previous`)는 그날까지의 데이터로만 계산하므로, 하루 전에 실행했을 때의 결과와 같습니다(테스트로 확인).
- 기준값은 2026-10-15에, 결과를 보기 전에 고정했습니다(`daily-v1`). 바꾸면 방법 버전을 올립니다.
  - 국면 변경, 새 원시 신호(전환 대기 n/3일), 위험 등급 경계 통과
  - 위험 점수 ±10점 이상. **구성요소 집합이 같은 날끼리만** 비교하고, 다르면 "구성요소 변경 · 점수 비교 불가"로 표시합니다.
  - 추세 점수 ±2 이상, 20일 변동성 백분위 0.70선 통과
  - 미국 10년물 금리 ±10bp, USD/KRW ±1% (전 관측일 대비)
- **화면:** Regime 카드에 "전일 대비" 목록과 "국면 판정 근거"(규칙별 값·변동성 백분위·전환 진행도)가 나옵니다. 브리핑 맨 앞에는 "0 · Daily Summary"가 나옵니다.
- **스냅샷:** `daily_snapshots` 테이블(스키마 v3)에 그날 보여준 결과를 추가 전용으로 남깁니다. 같은 날 같은 내용은 한 번만 저장하고, 데이터 수정으로 내용이 바뀌면 새 행으로 추가합니다. FRED 시장은 라벨만 저장하고 수치는 저장하지 않습니다. 계산에는 쓰지 않는 감사 기록입니다.
- **마이그레이션:** 데이터가 있는 기존 DB는 스키마를 올리기 전에 `data/backups/`로 자동 백업합니다.

## D. Data Sources & 라이선스

2026-10-15에 각 약관 페이지를 직접 확인했습니다. 법률 자문이 아니며, 확인되지 않은 권한은 허용으로 보지 않습니다. 같은 표가 대시보드 Model Performance / System Status 탭에 표시됩니다(`scripts/mi/licenses.py`).

| Provider / Dataset | 약관 | 수집 | 로컬 저장 | ML 학습 | 재배포 | 비고 |
| --- | --- | --- | --- | --- | --- | --- |
| S&P DJI via FRED · SP500 | [FRED legal](https://fred.stlouisfed.org/legal/) | 조건부 | 불가 | **불가** | 불가 | FRED: "development or training of any … machine learning" 금지, 저장·캐시·보관 금지. S&P 사전 서면 허가 필요 |
| Nasdaq via FRED · NASDAQ100 | 동일 | 조건부 | 불가 | **불가** | 불가 | "Copyrighted: Pre-Approval Required" |
| KRX OPEN API · KOSPI/KOSDAQ | [이용약관](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp) | 허용(키) | 조건부 | 허용: 약관에 ML 조항이 없고, 이용 신청 목적을 연구·머신러닝으로 기재해 승인받음(사용자 확인 2026-10-15, `licenses.SOURCES['krx_openapi'].ml_basis`) | 불가 | 비상업적 이용만, 제3자 제공 금지, 계약 종료 후 사용 불가 |
| Federal Reserve Board · H.15 / H.10 (DDP) | [disclaimer](https://www.federalreserve.gov/disclaimer.htm) | 허용 | 허용 | 허용 | 허용(출처 표기) | 공공 도메인. H.10은 주간 게시 |
| Cboe via FRED · VIXCLS | FRED legal | 조건부 | 불가 | 불가 | 불명확 | 기본 비활성 (옵트인 표시 전용) |
| ICE via FRED · HY/IG OAS | FRED legal | 조건부 | 불가 | 불가 | 불가 | 기본 비활성 (옵트인 표시 전용) |
| Federal Reserve · 보도자료 RSS, FOMC 일정 | disclaimer | 허용 | 허용 | 허용 | 허용 | 제목·링크·시각만 사용 |
| U.S. SEC · 보도자료 RSS | [sec.gov](https://www.sec.gov/privacy) | 조건부(UA·10req/s) | 허용 | 허용 | 허용 | |
| GDELT DOC 2.0 | [about](https://www.gdeltproject.org/about.html) | 허용(5초당 1회) | 허용 | 허용 | 조건부 | 기사 제목·URL 권리는 언론사 → 본문 미저장 |
| ProsusAI/finbert (선택) | [HF](https://huggingface.co/ProsusAI/finbert) | — | — | — | — | CC BY-NC-SA 4.0, 개인·비상업 |
| snunlp/KR-FinBert-SC | HF | — | — | — | — | 라이선스 미표기 → 사용 안 함 |
| Naver 뉴스 검색 API | developers.naver.com | — | 저장 금지 | — | — | 저장·가공 금지 약관 → 미연동 |
| Stooq | — | 미확인 | 미확인 | 미확인 | 미확인 | 약관 페이지 확인 불가 → 미사용 |

**⚠ 기존 동작에 대한 확인 필요 사항.** FRED 약관에는 "Store, cache, or archive any portion of FRED Content" 금지 조항이 있습니다. v0.2부터 `data/markets.json`에 FRED 종가 약 2년치를 저장해 왔는데, 이 동작은 그대로 두었습니다(기존 기능 보존). 이것이 개인 비상업 이용 범위에 해당하는지는 사용자가 판단해야 합니다. v1.0은 FRED 데이터를 추가로 보관하지 않습니다.

Copyright © S&P Dow Jones Indices LLC / Nasdaq, Inc. / Korea Exchange. 데이터는 FRED, KRX, Federal Reserve Board를 통해 조회했으며 이들의 승인·보증을 의미하지 않습니다. Yahoo 비공식 엔드포인트는 약관상 자동 접근 제한이 있어 사용하지 않습니다.

## F. Test Results

2026-10-15 이 환경(WSL2, Python 3.13, Node 24)에서 실제로 실행한 결과입니다.

```sh
make test      # 또는 아래 세 명령
cd scripts && python3 test_data.py                                            # v0.2: 12 tests OK
cd scripts && ../.venv/bin/python -m unittest test_mi_forecasting test_mi_analysis test_mi_cli  # v1.0: 98 tests OK
node --test scripts/test_intel.mjs                                            # intel.js: 9 tests OK
node --check real-data.js && node --check intel.js                            # OK
MI_CHROMIUM=<chromium> .venv/bin/python scripts/ui_smoke.py                    # 24 views, 2 widths: OK
```

검사 범위:
- **라이선스 게이트:** FRED·KRX·미등록 소스 거부, 불완전 manifest 거부, 비허용 입력의 피처 제외.
- **미래 정보 누출:** 미래 가격을 바꿔도 과거 피처·임계값·국면 이력이 불변, 한국 예측이 당일 미국 종가를 보지 않음(서머타임 전환 포함), 거시 게시 지연.
- **검증 절차:** purge된 fold 경계, 보정기 확률 합 = 1, 지표 계산값, 랜덤워크 미검증 / 신호 검증, 방향 정보 없는 모델 미검증.
- **예측 이력:** 추가 전용(같은 버전·기준일 중복 없음, 새 버전은 행 추가), Pending → 판정.
- **v0.2 공식 일치:** SMA·RSI·MACD·변동성.
- **수집:** 국면 분류·확인 규칙, 거시 파싱·재시도·실패 시 이력 보존·개정 기록, FRED 미보관.
- **뉴스·일정·브리핑:** 뉴스 중복 제거·멱등 수집·출처별 실패 격리, FOMC 파싱·실패 처리, 브리핑 섹션, LLM 옵트인과 수치 검증(모의 클라이언트), 작업 실패 기록.

UI 스모크(headless Chromium)는 REAL/DEMO × 6탭 × 2시장 × 3기간 × 데스크톱·모바일(390px)에서 콘솔 오류, 가로 넘침, 빈 화면을 검사합니다. 실데이터, 그리고 모든 패널이 채워진 합성 픽스처(스크래치 디렉터리, `MI_DATA_DIR`) 양쪽에서 통과했습니다.

실데이터 파이프라인(`make all`) 결과:
- 가격: S&P 500·NASDAQ 100 정상(2026-10-14), KRX는 키 없음으로 종료 코드 1(정상 동작).
- 거시: Fed 5개 시리즈 약 6,700행.
- 국면: 두 미국 지수 모두 Bullish / Low Volatility, 위험 26.9 / 38.6.
- 학습·예측: 게이트 차단.
- 뉴스: Fed·SEC 45건 수집, GDELT는 429 / 시간 초과.

## G. Limitations

- **예측 기능이 실데이터에서 비활성입니다.** 활성화하려면 ML 학습 권한이 명시된 지수 데이터 계약이 필요합니다(유료 가능성 높음, 승인 없이 진행하지 않았습니다).
- 라이선스 데이터가 있더라도 일별 지수 방향 예측은 기준 모델을 이기기 어렵습니다. 검증 기준을 통과하지 못하는 결과가 정상일 수 있습니다.
- 여러 모델을 비교하므로 다중 비교로 인한 낙관 편향이 약간 남습니다. 주 모델은 개발 구간에서만 선택해 이를 줄였습니다.
- **국면·위험 점수:** 사전에 정한 규칙이며 미래 수익률 예측력은 검증하지 않았습니다. 위험 점수의 가중치(균등 평균)와 매핑 상수는 판단에 따른 값입니다.
- **뉴스 감성:** 영문 키워드 휴리스틱이라 정확도가 낮습니다. 한국어는 미산출입니다. 과거 뉴스 아카이브가 없어 예측 피처로 쓰지 않습니다.
- **기대 판정일:** 주말만 제외한 추정입니다. 실제 판정은 h번째 다음 관측일로 합니다.
- **데이터 지연:** H.10 환율은 최대 약 1주 늦습니다. FRED는 다음 날 게시됩니다.
- **자동 실행:** 컴퓨터가 켜져 있어야 합니다.
- **향후 과제:**
  - 라이선스 데이터 확보 후 실제 holdout 평가
  - 한국은행 ECOS(공공데이터법상 출처 표기 조건) 연동 검토
  - 필터링 전용 HMM(라이선스 데이터에 한해)
  - 뉴스 피처의 추가 성능 검증

## H. Version Roadmap (실제 변경 사항)

| 버전 | 변경 |
| --- | --- |
| v0.2 (기존) | FRED/KRX 수집기, 기술 지표, REAL/DEMO UI. 문서에는 v0.3으로 언급되었으나 실제 저장소는 v0.2였습니다 |
| v0.4 | `mi/indicators.py`(인과적 지표, v0.2 공식과 일치), `mi/regime.py`(5개 국면, 3일 확인, 위험 점수, 요인), `mi/macro.py`(Fed DDP, 재시도·분할 수집), `mi/timing.py` |
| v0.5 | `mi/licenses.py`(권한 레지스트리·게이트·사용자 manifest), `mi/dataset.py`, `mi/models.py` |
| v0.6 | `mi/validation.py`, `mi/forecasting.py`(백테스트·실시간 예측 저장·판정) |
| v0.7 | `mi/news.py`, `mi/sentiment.py`, `mi/events.py` |
| v0.8 | `mi/briefing.py` (규칙 기반 + 옵트인 LLM) |
| v0.9 | `mi/store.py`(SQLite), `scripts/mi_cli.py`, `Makefile`, cron 예시 |
| v1.0 | `intel.js`, `index.html` 탭·스타일 확장, `scripts/ui_smoke.py` |

위 번호는 모듈이 추가된 순서입니다. 다음 릴리스부터는 `ROADMAP.md`의 번호(v0.4 Daily Market Intelligence, v0.5 Market Forecasting, v0.6 AI News Intelligence)를 따릅니다. 이 번호는 이미 구현된 기능을 제외한 남은 작업만 다룹니다.

---

## v0.2 수집기 상세 (기존 동작 유지)

`scripts/collect_data.py`는 표준 라이브러리 + 시스템 `curl`만 사용하며 `make ingest`가 내부에서 호출합니다. 단독 실행(`python3 scripts/collect_data.py`)도 가능합니다.

- FRED: 약 800일 일별 종가.
- KRX: 최초 약 420일, 이후 마지막 관측일 7일 전부터 재조회.
- 보간·대체는 하지 않습니다.
- 공급자별 실패는 다른 시장을 막지 않고, 이전 정상 이력을 `stale`로 유지합니다.
- JSON은 원자적으로 교체합니다. 기존 JSON이 손상돼 있으면 덮어쓰지 않습니다.
- 종료 코드: `0` 전체 성공, `1` 일부 실패, `2` 전체 실패.

지표 공식:
- SMA: N개 평균.
- RSI 14: Wilder 평활.
- MACD 12/26/9: SMA로 초기화한 EMA.
- 20일 수익률: 배당 미포함.
- 실현변동성: 20개 로그수익률 표본표준편차 × √252.

v1.0 엔진(`mi/indicators.py`)은 같은 공식을 사용하며, 테스트로 일치를 확인합니다.

KRX는 [공식 이용 절차](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp)에 따라 인증키와 **KOSPI/KOSDAQ 각 API 활용 승인**이 필요합니다. 키는 환경변수 `KRX_API_KEY`로만 전달하세요(HTML·JSON·소스에 넣지 마세요).

## KRX 요청 실패 진단

문서의 JSON 요청 예시에 맞춰 KRX 요청을 `POST` + `Content-Type: application/json` + `{"basDd":"YYYYMMDD"}`로 전송합니다. `AUTH_KEY`는 헤더에만 전달합니다. 업로드된 명세에는 HTTP 메서드가 명시되어 있지 않으므로 POST 전환만으로 오류 해결을 보장하지 않습니다.

키를 설정한 **같은 터미널**에서 먼저 하루만 진단하세요. 과거 이력을 수백 회 수집하지 않으며 기존 JSON을 변경하지 않습니다.

```sh
python3 scripts/collect_data.py --check-krx 2026-10-14
```

- `OK`: 해당 지수의 실제 종가 응답을 확인했습니다. 일반 수집 명령 실행 후 JSON 다시 읽기를 누르세요.
- `HTTP 401`: 키 유효성을 확인하세요.
- `HTTP 403`: 인증키 승인과 해당 API 활용 승인 및 접근 정책을 확인하세요.
- `HTTP 400 / 405`: 요청 형식 또는 HTTP 메서드를 확인하세요.
- `HTTP 429`: 요청 한도 초과입니다. 잠시 후 재시도하세요.
- `HTTP 000` 및 curl 5/6/28/56: DNS·프록시·연결·응답 수신 문제일 수 있습니다. 인증 실패로 단정하지 마세요.
- 데이터 없음: 휴장일 또는 아직 게시되지 않은 날짜인지 확인하세요.

키가 승인됐더라도 사용자 터미널에서 설정한 환경변수는 Codex 실행 프로세스로 자동 전달되지 않습니다. 진단 오류를 공유할 때 키는 포함하지 마세요.

### 일부 시장만 다시 수집

시간 초과로 KOSPI만 실패했다면, 키를 설정했던 같은 터미널에서 실행하세요.

```sh
python3 scripts/collect_data.py --markets ks
```

선택하지 않은 시장은 요청하지 않고, 기존 값·이력·상태·수집 시각을 그대로 보존합니다. 시장 코드는 `sp`(S&P 500), `nq`(NASDAQ 100), `ks`(KOSPI), `kq`(KOSDAQ)입니다. 최초 수집 도중 실패한 시장은 아직 저장된 완전한 이력이 없으므로 해당 시장 이력을 처음부터 다시 요청합니다.

일시적인 DNS·연결·응답 시간 초과와 HTTP 500/502/503/504는 요청당 최대 3회 시도합니다(1초·2초 대기). 인증/권한 오류(401/403), 한도 초과(429)는 자동 재시도하지 않습니다. 반복 실패 시 마지막 정상 데이터는 유지하며, 오류에 실패 기준일을 포함합니다.
