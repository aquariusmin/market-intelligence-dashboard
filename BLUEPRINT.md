# Future Architecture Blueprint

현재 구현과 분리된 향후 설계 제안입니다.

v0.2에서는 Python 수동 수집 → JSON → 기존 Vanilla JS 화면까지 연결했습니다. 미국 지수는 FRED, 한국 지수는 승인 필요 KRX 어댑터를 사용합니다. FRED의 AI/ML 개발·훈련 사용 금지 조건 때문에 아래 모델 계획에는 현재 FRED 데이터를 재사용하지 않습니다. 모델 개발 전에 훈련·파생·배포 권한이 확보된 별도 공급원을 선정해야 합니다.

## 전체 구조와 데이터 흐름

```text
Data Sources
    ↓
Data Collection
    ↓
Feature Engineering
    ↓
Machine Learning
    ↓
Forecast Engine
    ↓
API
    ↓
Dashboard
```

- Frontend: Next.js + TypeScript + Tailwind CSS
- Backend: Python + FastAPI
- Database: SQLite → PostgreSQL / Supabase
- Machine Learning: scikit-learn + LightGBM
- News Intelligence: FinBERT + Optional LLM
- Charts: TradingView Lightweight Charts + Recharts

정기 수집 → 검증·저장 → 시점별 변수 생성 → 모델 추론·확률 보정 → 버전별 예측 저장 → API 조회 순서로 처리합니다.

## 필요한 데이터

네 지수의 OHLCV, 거래소 캘린더, VIX, 미국 2년/10년 금리, CPI, DXY, USD/KRW, 신용 스프레드, 구성 종목 breadth와 실적 일정이 필요합니다. 뉴스는 이용권을 확보한 본문과 발행·수집 시각, 카테고리, 관련 시장을 저장합니다. 모든 데이터에 제공자·관측 시각·공개 시각·수정 이력을 기록합니다. 휴장일·시차·결측과 경제지표 수정치를 처리하며 미래 정보 누출을 방지합니다.

## 향후 API

- `GET /api/v1/markets`: 시장 요약
- `GET /api/v1/markets/{symbol}/prices?from=&to=`: 가격 이력
- `GET /api/v1/markets/{symbol}/forecast?horizon=5`: 확률·국면·변수·모델 버전
- `GET /api/v1/markets/{symbol}/indicators`: 수치·단위·설명
- `GET /api/v1/news?category=&market=`: 뉴스 분석
- `GET /api/v1/models/performance?market=&horizon=`: 모델 비교·성능·보정
- `GET /api/v1/forecasts/history?market=&horizon=`: 예측 시각·만기·관측 결과

응답에 `as_of`, `data_status`, `model_version`을 포함하고 지연 데이터와 미만기 예측을 표시합니다.

## 실제 예측 모델 개발 계획

1. 1/5/20거래일 선행 수익률에 상승·횡보·하락 라벨을 정의합니다. 횡보 임계값은 훈련 구간의 변동성과 비용을 고려합니다.
2. 수익률·이동평균 거리·RSI·MACD·변동성·금리·환율·breadth를 공개 시각 기준으로 정렬합니다.
3. Logistic Regression 기준 모델과 LightGBM, Ensemble을 비교합니다.
4. 시간 순서 기반 walk-forward 평가를 수행합니다. 겹치는 선행 수익률에는 purge/gap을 적용하고 튜닝·확률 보정·최종 평가 구간을 분리합니다.
5. Accuracy, Balanced Accuracy, Brier Score, Log Loss, Calibration을 시장·기간별로 평가합니다. 단순 기준 모델과 비교하고 거래 비용을 반영한 전략 평가는 별도로 수행합니다.
6. FinBERT로 뉴스 감성을 추출하고 Optional LLM은 출처가 연결된 설명 생성에 사용합니다. 확률 산출과 설명 생성을 구분합니다.
7. 입력·예측·모델 버전을 기록하고 데이터 신선도와 드리프트를 모니터링합니다.

## 기존 사이트 통합

기존 abovemin.com의 라우팅·스타일·인증 구조를 확인한 후 `/lab/markets`에 실제 프런트엔드를 연결합니다. 공통 디자인 토큰을 적용하고 별도 FastAPI 서비스는 서버 측 프록시로 연결합니다. 키를 클라이언트에 노출하지 않으며 개인용 접근 제어와 요청 제한을 추가합니다. 이번 작업에서는 해당 기술 구축·기존 사이트 변경·배포를 수행하지 않습니다.
