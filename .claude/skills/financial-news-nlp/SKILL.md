---
name: financial-news-nlp
description: Build or check financial news and briefing NLP — source terms (metadata only, no article bodies), deduplication, event classification, language-appropriate sentiment (FinBERT for English only, no unlicensed Korean model), importance, LLM-optional Korean briefing with schema and number validation, and keeping news out of forecast features until validated. Use when editing mi/news.py, mi/sentiment.py, mi/events.py or mi/briefing.py, adding a news source, or wiring an LLM.
---

# Financial News NLP

공통 원칙은 `CLAUDE.md` 원칙 1·2·7·8을 따른다. 새 소스의 약관 확인은 `financial-data-audit` 2단계를 그대로 쓴다. Claude API 코드를 작성할 때는 먼저 `claude-api` Skill을 로드한다.

## 실행 조건
- 뉴스 소스 추가, 중복 제거, 분류 규칙, 감성 방법, 중요도, 브리핑 섹션, LLM 경로를 변경할 때
- 뉴스를 예측 피처로 쓰자는 요청을 받았을 때. 이때는 아래 6단계를 먼저 따른다.

## 절차
1. **저장 범위.**
   - 제목, URL, 출처, 발행 시각, 수집 시각만 저장한다. 본문은 약관이 허용할 때만 저장하며, 현재 허용된 소스는 없다.
   - `news` 테이블에 body 계열 컬럼이 없어야 한다(테스트 있음).
   - 요청 빈도 제한을 지킨다: GDELT는 5초당 1회, SEC는 User-Agent를 붙이고 초당 10회 이하.
2. **중복 제거.**
   - 정규화한 URL로 1차 제거하고, 48시간 안의 제목 토큰 Jaccard ≥ 0.8로 2차 제거한다.
   - 중복 기사도 삭제하지 않고 `duplicate_of`로 연결한다.
   - 새 소스에는 같은 기사가 여러 매체에 실린 경우를 테스트 케이스로 추가한다.
3. **분류와 중요도.**
   - 이벤트 범주는 `news.CATEGORIES` 9개와 Other다.
   - 규칙을 바꾸면 영문과 한글 예시 제목으로 단위 테스트를 추가한다.
   - LLM으로 분류할 때는 다음을 지킨다.
     - 구조화 JSON 출력을 받는다.
     - 범주 enum과 필드 타입을 검증한다.
     - 검증에 실패하면 규칙 기반 분류로 되돌린다.
     - LLM 호출은 비용이 들므로 옵트인 플래그를 둔다.
4. **감성.**
   - 영문: 기본은 `lexicon-en-v2` 휴리스틱으로, 낮은 신뢰도임을 표시한다. `MI_FINBERT=1`이면 ProsusAI/finbert(CC BY-NC-SA, 비상업)를 쓴다.
   - 한국어: 라이선스와 학습 데이터가 확인된 모델이 없으면 `unavailable-ko`로 둔다. 영문 모델을 한국어에 적용하지 않는다.
   - 새 모델을 들이려면 `licenses.MODELS`에 라이선스, 용도 제한, 출처를 기록한다. 가능하면 라벨된 샘플로 정확도를 측정해 보고한다.
5. **브리핑과 LLM.**
   - 확률은 반드시 `forecast.json`에서 복사한다. LLM은 확률, 전망, 새 사실을 만들지 않는다.
   - `briefing.validate_llm`처럼 스키마와 수치를 검증한다(입력에 없는 숫자가 있으면 거부하고 규칙 기반 문장으로 되돌린다).
   - `ANTHROPIC_API_KEY`가 없거나 `--llm` 플래그가 없으면 호출하지 않는다.
   - 인용한 뉴스와 데이터는 `sources`와 링크로 연결한다.
   - 단위 테스트에서는 모의 클라이언트만 쓴다. 실제 호출은 사용자 승인 후에만 한다.
6. **예측 모델과의 분리.**
   - 뉴스 피처를 모델에 넣으려면 다음이 모두 갖춰져야 한다.
     - 발행 시각이 정확한 과거 아카이브(최소 수년치), 그리고 게시 시각 ≤ decision_time 조인
     - 동일 fold에서 뉴스 없는 모델과 비교(`quant-model-review` 기준)
     - `time-series-validation`을 통과한 누출 검사
   - 그 전에는 해석용으로만 쓴다.
7. **실행.**
   - `cd scripts && ../.venv/bin/python -m unittest test_mi_analysis`(`NewsTest`, `EventsBriefingTest`)
   - 실수집 점검: `mi_cli.py news [--no-gdelt]`. 소스별 `status`와 `error`를 확인한다.

## 출력 형식
```
## News/NLP Change Report — <변경 요약>
| 항목 | 결과 |
|---|---|
| 소스·약관 (저장 범위) | 소스별 허용 범위, 확인일 |
| 수집 결과 | 소스별 ok/failed, 추가 건수, 중복 건수 |
| 분류 / 감성 방법 | 규칙/모델명·라이선스, 언어별 적용 여부 |
| 브리핑 검증 | 섹션 생성, 수치 검증, LLM 사용 여부(호출 수·승인) |
| 예측 모델 분리 | 유지 / (통합 시) 비교 검증 결과 |
테스트: 실행 명령과 결과
```

## 실패 시 처리
- **소스 실패(HTTP 429 / 403 / 시간 초과):** 해당 소스만 failed로 기록하고 나머지는 계속 처리한다. 429는 재시도하지 않는다(현재 GDELT가 429를 반환한 이력이 있다).
- **분류나 감성 결과가 불확실함:** neutral이나 Other로 두고 방법명을 표시한다. 그럴듯한 값을 지어내지 않는다.
- **LLM 실패, 거절, 검증 실패:** 규칙 기반 브리핑을 유지하고 `llm_status`에 사유를 남긴다.
