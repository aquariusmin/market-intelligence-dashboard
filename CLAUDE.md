# Market Intelligence Dashboard

미국·한국 4개 지수(S&P 500 `sp`, NASDAQ 100 `nq`, KOSPI `ks`, KOSDAQ `kq`)의 시세, 국면·위험, 거시, 뉴스, 한국어 브리핑을 보여주는 개인용 로컬 대시보드다. **데이터 권한과 시간순 검증을 통과한 경우에만** 1/5/20거래일 방향 확률을 보여준다. 자동매매·주문·계좌 기능은 범위 밖이다.
- 사용법·방법론·데이터 약관 표: `README.md`
- 구조: `ARCHITECTURE.md`
- 리뷰·결정 기록: `CODE_REVIEW.md`
- 남은 과제: `ROADMAP.md`

하나의 제품이며 단계별 버전으로 나누지 않는다. 기본 브랜치는 `main`이다.

## 아키텍처

- **가격 수집기** `scripts/collect_data.py`, `scripts/providers.py`: stdlib + curl. FRED(sp, nq)와 KRX(ks, kq)에서 받아 `data/markets.json`에 쓴다. 지표·간단 국면도 계산한다(`--recalculate`).
- **참고 지표 수집기** `scripts/collect_macro.py`: 선택 사항. FRED와 ECOS에서 받아 `data/reference_only/macro.json`에 쓴다. 표시 전용이며 학습에 쓰지 않는다.
- **분석 엔진** `scripts/mi/` (`.venv` 필요)
  - `timing.py`: available_at / decision_time, as-of 조인, 연준 휴일
  - `indicators.py`, `regime.py`, `daily.py`: 인과적 지표, 규칙 기반 국면·위험, 전일 대비 변화
  - `macro.py`: Fed H.15/H.10
  - `licenses.py`: 권한 레지스트리와 ML·저장 게이트
  - `prices.py`: 가격 시리즈. KRX와 토스 잠정치를 합치고, 미국 학습 대상은 ETF 대체(`target_series`), 화면용 최신값은 `latest_overlay`
  - `toss.py`: 토스증권 시세 전용 클라이언트(주문 경로 차단)
  - `backfill.py`: KRX 장기 이력
  - `dataset.py`, `models.py`, `validation.py`, `forecasting.py`: 학습과 예측
  - `economics.py`: 비용 반영 성과
  - `explain.py`: 전망 변화 설명
  - `news.py`, `sentiment.py`, `events.py`, `briefing.py`, `nlp_eval.py`: 뉴스와 브리핑 (`briefing`은 LLM 월 예산 검사)
  - `dart.py`: OpenDART 주요사항보고서 목록(보관 안 함, 표시 전용)
  - `store.py`: SQLite `data/mi.sqlite`
- **파이프라인** `scripts/mi_cli.py`: 진입점. `data/dashboard/*.json`에 쓴다.
- **화면** `index.html`(레이아웃, CSS, DEMO 샘플 화면) + `app.js`(REAL DATA 화면 전체). 빌드 단계가 없다. `scripts/serve.py`로 띄운다.
- `data/`는 gitignore 대상이다. 공급자 데이터와 그 파생물은 커밋하지 않는다.

## 개발 원칙 (모든 작업 공통 — Skills에서 반복하지 않음)

1. **데이터 무결성**: 실제 결과 자리에 합성·샘플·추정값을 쓰지 않는다. 없으면 `unavailable`과 사유를 표시한다. 보간이나 forward-fill로 거래일을 채우지 않는다. 합성 데이터는 테스트 안에서만 쓴다. 잠정치(토스 당일 종가)는 잠정이라고 표시하고, 판정에는 공식값만 쓴다.
2. **라이선스**: 모든 소스는 `licenses.SOURCES`에 수집 / 저장 / ML / 재배포 권한과 확인일을 기록한다. 확인되지 않은 권한은 불허로 본다. ML 학습은 `ml_allowed()` 통과 소스만, SQLite 보관은 `storage_allowed()` 통과 소스만 가능하다. FRED 데이터는 ML 학습이 금지되고 엔진 보관도 하지 않는다.
3. **미래 정보 누출 금지**: 피처는 `available_at ≤ decision_time`인 값만 쓴다(as-of 조인). 라벨 임계값과 국면은 t 시점까지의 데이터로만 계산한다. 한국 예측에는 당일 미국 종가를 쓰지 않는다. 결측 대체와 스케일링은 학습 구간 안에서만 적합한다. 진행 중인 세션의 가격을 종가로 쓰지 않는다.
4. **검증 우선**: 무작위 분할을 쓰지 않는다. 검증 기준, 임계값, 비용 규칙은 사전에 고정하고 holdout 결과를 보고 바꾸지 않는다. 이미 holdout을 본 모델의 입력을 바꾸지 않는다. 성능이 나빠도 숨기지 않는다. 검증을 통과하지 못한 확률은 "신뢰할 수 없음"으로 표시한다.
5. **이력 보존**: 기존 관측값, 예측 이력, 사용자 데이터를 삭제하거나 덮어쓰지 않는다. 예측은 추가만 하고, 백테스트와 실시간 예측을 분리한다. 값이 바뀌면 `observation_revisions`에 기록한다.
6. **동작 보존**: DEMO 모드와 가격 수집기의 CLI·출력 형식을 유지한다. 리팩터링할 때는 테스트와 브라우저 스모크로 기존 동작을 확인한다.
7. **비밀정보**: API 키는 환경변수로만 다룬다(`KRX_API_KEY`, `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`, `ANTHROPIC_API_KEY`, `ECOS_API_KEY`, `DART_API_KEY`). 토스 토큰은 주문 권한까지 있으므로 `mi/toss.py`의 시세 경로 허용 목록을 넓히지 않는다. 키를 코드, JSON, 로그, 커밋 메시지에 남기지 않는다.
8. **승인 필요**: 유료 API나 데이터 계약, 유료 LLM 호출(`briefing --llm`), GitHub push, 외부 배포, abovemin.com 변경, 사용자 데이터 삭제.
9. **보고**: 실행하지 않은 테스트를 통과했다고 보고하지 않는다. 미완료 항목은 사유와 함께 밝힌다.

## 명령

`make`는 이 환경에 설치되어 있지 않다. 아래 명령을 직접 쓰거나, Makefile 타깃이 같은 명령을 감싼다.

```sh
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt   # make setup
.venv/bin/python scripts/mi_cli.py ingest|regime|train|forecast|news|briefing|status
.venv/bin/python scripts/mi_cli.py morning|evening|weekly|all|catchup            # 단계 실패 격리 · catchup = 놓친 작업 재실행
.venv/bin/python scripts/mi_cli.py backup | backfill [--since YYYY-MM-DD] | label-export | label-score
python3 scripts/serve.py --port 3000                                              # make dev → localhost:3000 (허용 파일만·Host 검사·CSP)
MI_DATA_DIR=<scratch dir> .venv/bin/python scripts/mi_cli.py ...                  # 실데이터와 격리된 실험
```

## 테스트 (변경 후 해당 범위를 실행하고 결과를 보고)

```sh
cd scripts && python3 test_data.py                                                # 가격·참고 지표 수집기 (18)
cd scripts && ../.venv/bin/python -m unittest test_mi_forecasting test_mi_analysis test_mi_cli # 엔진·CLI (113)
node --check app.js && node scripts/test_ui.js && node --test scripts/test_intel.mjs   # 화면 로직 (UI 8그룹, 10)
MI_CHROMIUM=~/.cache/ms-playwright/chromium_headless_shell-1247/chrome-headless-shell-linux64/chrome-headless-shell \
  .venv/bin/python scripts/ui_smoke.py [--root <fixture>] [--shots <dir>] [--expect "Tab=text"]
.venv/bin/python .claude/skills/financial-data-audit/scripts/audit_data.py        # 읽기 전용 데이터 감사
```

- 네트워크가 필요한 단위 테스트는 없다. 공급자 호출은 모두 mock으로 대체한다.
- UI 스모크는 REAL과 DEMO 모드 × 6탭 × 데스크톱과 390px 화면에서 콘솔 오류, 가로 넘침, 빈 화면을 검사한다.
- JS 테스트는 `index.html`의 인라인 스크립트와 `app.js`를 vm에서 그대로 실행한다.

## 프로젝트 Skills (`.claude/skills/`, 해당 작업일 때만 로드)

| Skill | 언제 |
| --- | --- |
| `financial-data-audit` | 새 데이터 소스, 수집기 변경, 데이터 품질·라이선스 점검 |
| `time-series-validation` | 피처, 라벨, 분할, 백테스트, 보정 코드 변경 |
| `quant-model-review` | 모델 추가·변경, 성능 결과 해석, 검증 결과를 승인하기 전 |
| `market-regime-analysis` | 국면·위험 규칙, HMM, 요인 문장 변경 |
| `dashboard-qa` | `index.html`, `app.js`, `data/dashboard/*.json` 스키마 변경 |
| `financial-news-nlp` | 뉴스 수집·중복 제거·분류·감성, 브리핑 문장 생성 변경 |

설치된 보조 도구:
- `code-review`: 일반 버그 리뷰. 금융·통계 관점 리뷰는 `quant-model-review`가 맡는다.
- `pyright-lsp`: `pyright` 바이너리가 PATH에 있어야 동작한다.
- `context7`: 라이브러리 문서 조회.
- `frontend-design`: 새 UI 방향을 정할 때만 쓴다. 기존 디자인 유지가 기본이다.
- `playwright-cli`: 대화형 브라우저 디버깅.
