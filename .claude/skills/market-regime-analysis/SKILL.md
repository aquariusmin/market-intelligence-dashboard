---
name: market-regime-analysis
description: Develop or check the market-regime and risk layer — rule-based Bullish/Bearish × Low/High volatility and Sideways classification, confirmation (hysteresis), risk score components, bullish/bearish factor sentences, regime history, optional filtered-only HMM. Use when editing mi/regime.py or mi/indicators.py, changing thresholds or risk mappings, adding macro risk inputs, or interpreting regime.json for a user.
---

# Market Regime Analysis

공통 원칙은 `CLAUDE.md` 원칙 1·3·4를 따른다. 국면은 **현재 상태를 서술**하는 것이지 미래 수익률을 예측하는 것이 아니다.

## 실행 조건
- `TREND_RULES`, `TREND_BULL/BEAR`, `VOL_HIGH_PCT/ABS`, `CONFIRM_DAYS`, `RISK_BANDS`, `risk_components`, `factors()`를 변경할 때
- 새 위험 입력(VIX, 신용스프레드, 환율 등)을 추가할 때, HMM을 도입할 때
- `regime.json`의 해석을 요청받았을 때

## 절차
1. **규칙 변경 전 문서화.** 변경 내용과 근거를 `regime.py` 상단 상수 주석과 README C/G 절에 먼저 적는다. 과거 수익률 예측력을 높이려고 임계값을 데이터에 맞춰 튜닝하지 않는다. 튜닝은 곧 모델 학습이므로 라이선스 게이트 대상이다.
2. **인과성.**
   - 모든 지표는 `indicators.frame`의 rolling(과거만) 계산을 쓴다.
   - 백분위는 `trailing_percentile`을 쓴다. 전체 표본 분위수는 금지다.
   - 거시 입력은 `macro_frame`의 as-of 조인을 쓴다.
   - 확인 규칙 `confirm()`은 과거 라벨만 본다.
   - 섭동 테스트(`RegimeTest.test_history_is_point_in_time`)가 통과해야 한다.
3. **HMM(선택).**
   - 라이선스상 ML이 허용된 데이터에서만 적합한다. FRED 가격에는 적합하지 않는다.
   - 과거 각 시점의 상태는 그 시점까지 적합한 모델의 **filtered** 확률(forward 알고리즘)로만 표시하고 피처로 쓴다.
   - smoothed(forward-backward) 상태는 "사후 분석"으로 라벨을 붙여 별도 표시하며, 피처로 쓰지 않는다.
   - 상태 번호가 바뀌는 문제(label switching)는 평균 수익률이나 변동성 순서로 고정한다.
4. **위험 점수.**
   - 각 구성요소는 0–1 범위의 고정 매핑이다. 결측 구성요소는 평균에서 제외하고 UI에 "미수집"으로 표시한다.
   - 시장별 적용 범위를 확인한다. 예: KRW는 ks와 kq에만 적용한다.
5. **요인 문장.**
   - 현재 수치와 기준값만으로 문장을 만든다. "상승할 것" 같은 예측 표현을 쓰지 않는다.
   - 문장 안의 숫자는 metrics 값과 같아야 한다.
6. **검증.**
   - `cd scripts && ../.venv/bin/python -m unittest test_mi_analysis` 를 실행한다.
   - 실데이터로 `mi_cli.py regime`을 실행해 마지막 행 지표를 v0.2 스냅샷과 대조한다(`IndicatorParityTest`).
   - 전환 빈도도 점검한다. 확인 규칙을 적용한 뒤에도 1–2일짜리 전환이 많으면 규칙을 검토한다.

## 출력 형식
```
## Regime Report — <시장> (기준일 <as_of>, source <source_id>)
국면: <regime> (원시 <raw_regime>, <regime_since>부터 <regime_days>일, 추세점수 ±n/5)
단기 / 중기 추세: … / …
위험: <level> <score>/100 — 구성요소: 변동성 x · 낙폭 x · 금리 x · …(미수집 표시)
상승 요인 / 위험 요인: (factors 그대로)
최근 전환: 날짜 · 이전 → 이후
주의: 국면은 현재 상태 서술이며 예측이 아님
```
코드 변경 리뷰라면 위 보고서 대신 "변경 규칙 · 근거 · 인과성 테스트 결과 · 전환 빈도 전후 비교"를 보고한다.

## 실패 시 처리
- **이력 부족(200일선이나 60일 모멘텀 계산 불가):** `insufficient_history`를 그대로 표시한다. 짧은 창으로 대체하지 않는다.
- **거시 입력 결측:** 해당 위험 구성요소를 None으로 두고 점수는 나머지로만 계산한다. 결측 사실은 표시한다.
- **v0.2 지표와 불일치:** 공식 차이를 먼저 해결한다. 해결 전에는 변경을 반영하지 않는다.
