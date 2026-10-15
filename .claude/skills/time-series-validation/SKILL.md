---
name: time-series-validation
description: Verify that forecasting features, labels, splits, backtests and probability calibration are leakage-free and chronologically valid — walk-forward, purging/embargo for overlapping 5D/20D horizons, train-only preprocessing, dev/holdout separation, calibration on a later slice. Use when editing mi/dataset.py, mi/timing.py, mi/validation.py, mi/models.py calibrators or mi/forecasting.py, or when adding any feature or label.
---

# Time-Series Validation

공통 원칙은 `CLAUDE.md` 원칙 3·4를 따른다. 이 Skill은 변경된 코드가 시간 순서를 지키는지 **증명**하는 절차다. 모델이 좋은지 판단하는 일은 `quant-model-review`가 맡는다.

## 실행 조건
- 피처·라벨·임계값을 추가하거나 바꿀 때, 분할·fold·purge·embargo·보정 로직을 바꿀 때, 새 시장이나 새 예측 기간을 추가할 때
- 대상이 아님: 국면 규칙(→ `market-regime-analysis`), 데이터 품질 자체(→ `financial-data-audit`)

## 절차
1. **피처 시점 검사.** 새 피처마다 아래 셋을 확인한다.
   - (a) 자기 시장 값은 행 t에서 `close[0..t]`만 사용하는가. rolling 창은 반드시 `min_periods`를 지정하고, `center=True`·`shift(-k)`·`bfill`은 금지다.
   - (b) 외부 시장·거시 값이 `timing.asof_join(decision_times, …, available_at, …)`을 거치는가.
   - (c) 정규화·결측 대체를 모델 파이프라인 안(fold의 학습 구간)에서 적합하는가. 전체 표본 통계를 쓰면 안 된다.
2. **섭동 테스트(필수).** `test_mi_forecasting.LeakageTest` 패턴을 따른다.
   - 위치 k 이후의 미래 가격이나 거시값만 바꾸고 `dataset.build`를 다시 실행한다.
   - `X.iloc[:k]`와 `threshold.iloc[:k]`가 완전히 같아야 한다.
   - 새 소스에는 교차시장 시각 테스트를 추가한다. 예: 한국 t행은 미국 t-1 값만 보는지, 서머타임 전환 구간을 포함한다.
3. **라벨과 겹침.**
   - 라벨은 \(R_{t,h}=P_{t+h}/P_t-1\)이다. Neutral 대역은 t 시점까지의 σ와 사전 고정 k(0.43)를 쓴다.
   - k나 대역을 바꾸려면 데이터를 보기 전에 근거를 문서로 남긴다.
   - h>1이면 표본 i의 결과 위치는 `outcome_pos = i+h`이다. 다음 구간 시작 s보다 `outcome_pos + embargo ≤ s`일 때만 사용 가능해야 한다.
4. **분할 구조 확인.** `test_mi_forecasting.SplitTest`를 확장한다.
   - 순서는 개발 구간 → purge 간격(≥ h) → holdout이다.
   - 각 fold 안의 순서는 fit → purge → calibration → purge → test다. 모든 경계에서 겹침이 없어야 한다.
   - 무작위 분할, `KFold(shuffle=True)`, `train_test_split`이 쓰이지 않는지 grep으로 확인한다.
   - rolling 창(`Config.window`)을 쓸 때도 같은 조건을 지켜야 한다.
5. **보정.**
   - 보정기는 fit 구간보다 시간상 뒤인 구간에서 적합한다. 보정 방법은 개발 구간에서 선택한다.
   - holdout을 보정에 쓰면 안 된다.
   - 출력 확률은 각 행의 합이 1(±1e-9)이고 모든 값이 0보다 커야 한다(`models.CALIBRATORS` 테스트).
6. **음성 대조(필수).**
   - 순수 랜덤워크 합성 시계열에서 모든 모델이 `validated`가 되지 않아야 한다(`PipelineTest.test_random_walk_is_not_validated`).
   - 이 대조가 실패하면 누출 또는 검증 기준 결함으로 본다.
7. **실행.** `cd scripts && ../.venv/bin/python -m unittest test_mi_forecasting test_mi_analysis`

## 출력 형식
```
## Time-Series Validation — <변경 요약>
판정: LEAKAGE-FREE | ISSUES FOUND
| 검사 | 결과 | 증거 (테스트명 / 코드 위치) |
|---|---|---|
| 피처 인과성 (섭동) | ✓/✕ | test_… |
| as-of 정렬·게시 지연 | ✓/✕ | file:line |
| 학습구간 전처리 | ✓/✕ | |
| purge/embargo (h=1/5/20) | ✓/✕ | |
| 개발/holdout 분리 | ✓/✕ | |
| 보정 구간 순서·확률 합 | ✓/✕ | |
| 랜덤워크 음성 대조 | ✓/✕ | |
실행한 명령과 결과: (테스트 수 / OK 또는 실패 출력)
```

## 실패 시 처리
- **누출이 확인됨:** 해당 피처나 로직을 즉시 비활성화하거나 수정한다. 그 코드로 만든 `performance.json` 결과와 실시간 예측은 신뢰할 수 없다고 보고한다. 기록은 삭제하지 말고 재학습 필요 여부를 알린다.
- **판단이 불확실함:** 해당 피처를 제외하는 쪽으로 기본 결정한다(`dropped_features`에 사유를 기록).
- **테스트를 실행하지 못함:** 통과로 보고하지 않는다.
