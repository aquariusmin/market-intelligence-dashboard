---
name: quant-model-review
description: Statistical review of a forecasting model or backtest result before it is trusted or shown — baseline comparison, walk-forward holdout metrics, block-bootstrap significance, probability calibration (ECE, reliability), effective sample size, multiple-testing and overfitting checks, validation-gate decision. Use when adding/changing a model, hyper-parameter grid or validation criterion, when reading performance.json, or when the user asks "is this model good / should I trust this forecast".
---

# Quant Model Review

공통 원칙은 `CLAUDE.md` 원칙 4를 따른다. 누출 여부는 `time-series-validation`이 먼저 확인했다고 가정한다. 확인되지 않았다면 그 Skill부터 실행한다. 일반 코드 버그 리뷰는 `code-review` 플러그인을 쓴다.

## 실행 조건
- `models.GRID`·`FACTORY`·`validation.Config`·`gate()`를 변경할 때, 새 모델(RF, Ensemble, HMM 등)을 추가할 때
- `data/dashboard/performance.json` 결과를 해석하거나 사용자에게 보고할 때
- 대상이 아님: 실데이터 학습이 라이선스 게이트에 막힌 상태. 이 경우 리뷰할 결과가 없음을 그대로 보고한다.

## 절차
1. **입력 확인.** `performance.json`의 `status`, `spans`, `primary_model`과 그 선택 규칙을 확인한다. 실데이터 결과인지(합성 픽스처가 아닌지)와 소스 권한도 확인한다(`gate`, `feature_sources`).
2. **기준 모델 비교(필수).** 비교 대상은 class_frequency, momentum_rule, logistic_regression이며 반드시 같은 fold에서 비교한다. 다음을 모두 본다.
   - log loss 개선 폭과 90% 블록 부트스트랩 CI. 블록 길이는 h 이상이다.
   - Brier skill
   - balanced accuracy 차이(방향 정보)
   - Macro F1, 혼동행렬. 한 클래스만 예측하는 퇴화 모델인지 확인한다.
3. **보정.** ECE와 클래스별 신뢰도 곡선을 본다.
   - 확률이 0.5를 넘는 구간에 표본이 충분한지 확인한다. 표본이 희박한 구간의 보정 결과는 신뢰하지 않는다.
   - 보정 방법이 개발 구간에서 선택되었는지 확인한다.
4. **표본과 안정성.**
   - `effective_n = n/h`가 50 이상이어야 한다.
   - rolling accuracy로 시기별 편차를 본다. 특정 국면에서만 성과가 나는지 확인한다.
   - 개발 구간 log loss와 holdout log loss의 격차로 과적합 정도를 확인한다.
5. **다중 비교·과적합.**
   - 시도한 모델·설정·피처 조합 수를 보고한다.
   - holdout을 본 뒤 설정, 기준, 피처를 바꾸었다면 그 holdout은 오염된 것이다. 새 holdout 기간이나 실시간 판정 결과로만 다시 평가한다.
   - 그리드나 피처를 늘릴 때는 개발 구간 안에서만 선택한다.
6. **검증 판정.**
   - `validation.gate`의 사전 고정 기준을 적용한다: 비중첩 표본 ≥ 50, log loss CI 하한 > 0, Brier skill > 0, balanced accuracy +2%p, ECE ≤ 0.08.
   - 기준을 바꾸려면 근거를 쓰고 결과를 보기 전에 커밋한다. 결과를 보고 기준을 완화하지 않는다.
7. **실시간 성과.** `forecast.json`의 `live_summary`에서 판정 완료 건수와 적중률을 확인한다. 30건 미만이면 결론을 유보한다.

## 출력 형식
```
## Model Review — <시장> <h>D (<run_id>)
결론: VALIDATED | NOT VALIDATED | NOT REVIEWABLE (사유)
| 모델 | Acc | BalAcc | MacroF1 | Brier | LogLoss | ECE | LL개선 90%CI | 판정 |
|---|---|---|---|---|---|---|---|---|
주요 근거 (3–5줄): 기준 대비 개선의 크기·유의성, 보정 상태, 표본 수, 안정성
위험 신호: 퇴화 예측 / 표본 부족 / 다중 비교 / 개발-holdout 격차 / 국면 의존
권고: 표시 방식(검증 배지 / 미검증 경고 / 비표시), 추가 검증
```

## 실패 시 처리
- **기준 미충족:** NOT VALIDATED로 보고하고 UI가 미검증 경고를 표시하는지 확인한다. 수치를 좋게 보이도록 재선택하거나 재튜닝하지 않는다.
- **결과 파일이 없거나 `status ≠ ok`:** NOT REVIEWABLE로 보고하고 사유(라이선스 게이트, 이력 부족)를 그대로 전달한다.
- **holdout 오염 의심:** 결과를 철회하고 새 평가 기간이 필요하다고 보고한다.
