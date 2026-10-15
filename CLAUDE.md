# Market Intelligence Dashboard

미국·한국 4개 지수(S&P 500 `sp`, NASDAQ 100 `nq`, KOSPI `ks`, KOSDAQ `kq`)의 국면·위험·거시·뉴스·한국어 브리핑과, **라이선스·시간순 검증을 통과한 경우에만** 1/5/20거래일 방향 확률을 보여주는 개인용 로컬 대시보드. 자동매매·주문·계좌 기능은 범위 밖. 상세 방법론·데이터 약관 표·실행 가이드는 `README.md`(A–H), 향후 설계는 `BLUEPRINT.md`.

현재 코드는 v1.0 로컬(`feature/v1.0-local` 브랜치)이다. 요청에 "v0.3"이라고 적혀 있어도 실제 구현 범위는 코드를 기준으로 판단한다.

## 아키텍처

- `scripts/collect_data.py`, `scripts/providers.py` — v0.2 가격 수집기(stdlib + curl). FRED(sp, nq), KRX(ks, kq; `KRX_API_KEY` 필요) → `data/markets.json`
- `scripts/mi/` — v1.0 엔진(`.venv` 필요)
  - `timing.py`: available_at / decision_time, as-of 조인
  - `indicators.py`: 인과적 지표, v0.2와 같은 공식
  - `regime.py`: 규칙 기반 국면·위험 (전일 상태 `previous` 포함)
  - `daily.py`: v0.4 전일 대비 변화(사전 고정 기준값)·국면 해설·일별 스냅샷
  - `macro.py`: Fed H.15/H.10
  - `licenses.py`: 권한 레지스트리 + ML 게이트
  - `dataset.py`: 피처·라벨
  - `models.py`: 기준 모델·LightGBM·보정기
  - `validation.py`: purged walk-forward·지표·검증 기준
  - `forecasting.py`: 백테스트 / 실시간 예측 저장
  - `news.py`, `sentiment.py`, `events.py`, `briefing.py`
  - `toss.py`: 토스증권 시세 전용 클라이언트(주문 경로 차단) · KOSPI/KOSDAQ 당일 종가, SPY/QQQ
  - `backfill.py`: KRX 장기 이력, `explain.py`: 검증 통과 모델의 전망 변화 설명, `nlp_eval.py`: 뉴스 라벨 평가
  - `store.py`: SQLite `data/mi.sqlite`
- `scripts/mi_cli.py` — 파이프라인 진입점 → `data/dashboard/*.json`
- `index.html`(v0.2 UI + DEMO) ← `real-data.js`(v0.2 REAL) ← `intel.js`(v1.0 패널). 빌드 단계 없음.
- `data/`는 gitignore 대상이다. 공급자 데이터와 그 파생물은 커밋하지 않는다.

## 개발 원칙 (모든 작업 공통 — Skills에서 반복하지 않음)

1. **데이터 무결성**: 실제 결과 자리에 합성·샘플·추정값을 쓰지 않는다. 없으면 `unavailable`과 사유를 표시한다. 보간이나 forward-fill로 거래일을 채우지 않는다. 합성 데이터는 테스트 안에서만 쓴다.
2. **라이선스**: 모든 소스는 `licenses.SOURCES`에 수집 / 저장 / ML / 재배포 권한과 확인일을 기록한다. 확인되지 않은 권한은 불허로 본다. ML 학습은 `ml_allowed()` 통과 소스만, SQLite 보관은 `storage_allowed()` 통과 소스만 가능하다. FRED 데이터는 ML 학습과 보관이 모두 금지다.
3. **미래 정보 누출 금지**: 피처는 `available_at ≤ decision_time`인 값만 쓴다(as-of 조인). 라벨 임계값과 국면은 t 시점까지의 데이터로만 계산한다. 한국 예측에는 당일 미국 종가를 쓰지 않는다. 결측 대체와 스케일링은 학습 구간 안에서만 적합한다.
4. **검증 우선**: 무작위 분할을 쓰지 않는다. 검증 기준과 임계값은 사전에 고정하며, holdout 결과를 보고 바꾸지 않는다. 성능이 나빠도 숨기지 않는다. 검증을 통과하지 못한 확률은 "신뢰할 수 없음"으로 표시한다.
5. **이력 보존**: 기존 관측값, 예측 이력, 사용자 데이터를 삭제하거나 덮어쓰지 않는다. 예측은 추가만 하고, 백테스트와 실시간 예측을 분리한다. 값이 바뀌면 `observation_revisions`에 기록한다.
6. **기존 기능 보존**: DEMO 모드와 v0.2 화면, v0.2 수집기의 동작과 인터페이스를 유지한다. 대규모 재작성을 하지 않는다.
7. **비밀정보**: API 키는 환경변수로만 다룬다(`KRX_API_KEY`, `ANTHROPIC_API_KEY`, `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`). 토스 토큰은 주문 권한까지 있으므로 `mi/toss.py`의 시세 경로 허용 목록을 넓히지 않는다. 키를 코드, JSON, 로그, 커밋 메시지에 남기지 않는다.
8. **승인 필요**: 유료 API나 데이터 계약, 유료 LLM 호출(`briefing --llm`), GitHub push, 외부 배포, abovemin.com 변경, 사용자 데이터 삭제.
9. **보고**: 실행하지 않은 테스트를 통과했다고 보고하지 않는다. 미완료 항목은 사유와 함께 밝힌다.

## 명령

`make`는 이 환경에 설치되어 있지 않다. 아래 명령을 직접 쓰거나, Makefile 타깃이 같은 명령을 감싼다.

```sh
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt   # make setup
.venv/bin/python scripts/mi_cli.py ingest|regime|train|forecast|news|briefing|status
.venv/bin/python scripts/mi_cli.py morning|evening|weekly|all                    # 단계 실패 격리
.venv/bin/python scripts/mi_cli.py backup | backfill [--since YYYY-MM-DD]          # SQLite 백업 / KRX 이력 백필(키 필요, 재개 가능)
python3 scripts/serve.py --port 3000                                              # make dev → localhost:3000 (허용 파일만·Host 검사·CSP)
MI_DATA_DIR=<scratch dir> .venv/bin/python scripts/mi_cli.py ...                  # 실데이터와 격리된 실험
```

## 테스트 (변경 후 해당 범위를 실행하고 결과를 보고)

```sh
cd scripts && python3 test_data.py                                                # v0.2 수집기 (12)
cd scripts && ../.venv/bin/python -m unittest test_mi_forecasting test_mi_analysis test_mi_cli # v1.0 엔진·CLI (98)
node --check real-data.js && node --check intel.js
node --test scripts/test_intel.mjs                                                # intel.js 단위 테스트 (9, vm으로 실제 파일 로드)
MI_CHROMIUM=~/.cache/ms-playwright/chromium_headless_shell-1247/chrome-headless-shell-linux64/chrome-headless-shell \
  .venv/bin/python scripts/ui_smoke.py [--root <fixture>] [--shots <dir>] [--expect "Tab=text"]
.venv/bin/python .claude/skills/financial-data-audit/scripts/audit_data.py        # 읽기 전용 데이터 감사
```

- 네트워크가 필요한 단위 테스트는 없다. 공급자 호출은 모두 mock으로 대체한다.
- UI 스모크는 REAL과 DEMO 모드 × 6탭 × 데스크톱과 390px 화면에서 콘솔 오류, 가로 넘침, 빈 화면을 검사한다.

## 프로젝트 Skills (`.claude/skills/`, 해당 작업일 때만 로드)

| Skill | 언제 |
| --- | --- |
| `financial-data-audit` | 새 데이터 소스, 수집기 변경, 데이터 품질·라이선스 점검 |
| `time-series-validation` | 피처, 라벨, 분할, 백테스트, 보정 코드 변경 |
| `quant-model-review` | 모델 추가·변경, 성능 결과 해석, 검증 결과를 승인하기 전 |
| `market-regime-analysis` | 국면·위험 규칙, HMM, 요인 문장 변경 |
| `dashboard-qa` | `index.html`, `real-data.js`, `intel.js`, `data/dashboard/*.json` 스키마 변경 |
| `financial-news-nlp` | 뉴스 수집·중복 제거·분류·감성, 브리핑 문장 생성 변경 |

설치된 보조 도구:
- `code-review`: 일반 버그 리뷰. 금융·통계 관점 리뷰는 `quant-model-review`가 맡는다.
- `pyright-lsp`: `pyright` 바이너리가 PATH에 있어야 동작한다.
- `context7`: 라이브러리 문서 조회.
- `frontend-design`: 새 UI 방향을 정할 때만 쓴다. 기존 디자인 유지가 기본이다.
- `playwright-cli`: 대화형 브라우저 디버깅.
