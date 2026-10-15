---
name: financial-data-audit
description: Audit market/macro data for this dashboard — point-in-time availability, licence permissions, missing trading days, staleness, revisions and look-ahead risk. Use when adding or changing a data source or collector (providers.py, collect_data.py, mi/macro.py, mi/prices.py, mi/news.py sources, mi/licenses.py), when importing data/licensed CSVs, or when the user asks whether data is trustworthy or usable for ML.
---

# Financial Data Audit

공통 원칙은 `CLAUDE.md` 개발 원칙 1·2·3·5를 따른다. 이 Skill은 데이터 감사 절차만 다룬다.

## 실행 조건
- 새 공급자·시리즈·뉴스 소스를 추가할 때, 또는 수집·파싱·검증 로직을 바꿀 때
- `data/licensed/`에 사용자 데이터를 등록하거나 manifest를 수정할 때
- "이 데이터 믿을 수 있나 / ML에 써도 되나"라는 질문을 받았을 때
- 대상이 아님: UI만 바꾸는 작업(→ `dashboard-qa`), 모델 성능 해석(→ `quant-model-review`)

## 절차
1. **자동 감사(읽기 전용).** `.venv/bin/python .claude/skills/financial-data-audit/scripts/audit_data.py [--data DIR] [--json]`
   - 검사 항목: 날짜 역행·중복, 미래일, 비유한 값, 주말 관측, 4평일을 넘는 누락, 지연(H.10은 주간이라 11일까지 허용), 최근 2년 이상 변동(8σ), as_of 불일치, 보관 금지 소스의 SQLite 존재, 예측 키 중복, manifest 누락
   - 네트워크와 쓰기 작업은 하지 않는다. 실험 데이터는 `MI_DATA_DIR` 또는 `--data`로 격리한다.
2. **라이선스 확인(새 소스에 필수).**
   - 공식 약관 URL을 직접 읽고 `licenses.SOURCES`에 다음 항목을 기록한다: `collection`, `local_storage`, `ml_training`, `redistribution`, `terms_url`, `verified_on`, 근거 인용을 담은 `notes`
   - 값은 `allowed / conditional / not_allowed / unclear / unverified`이다. 명시가 없으면 `unclear`로 두며, 게이트에서는 불허로 처리된다.
   - 원 저작권자 조건(예: S&P, ICE, Cboe)이 중계 사이트 약관보다 우선하는지 확인한다.
   - 기사 원문, 유료 데이터, 저장 금지 데이터(예: Naver 검색 결과)는 수집하지 않는다.
3. **시점 정합성(새 시리즈에 필수).**
   - 관측일 d의 값을 처음 알 수 있는 시각을 공식 게시 일정으로 확인한다.
   - `timing.MACRO_LAG`에 보수적인 규칙을 추가한다(불확실하면 늦게 잡는다).
   - 가격은 거래소 마감 시각(`timing.SESSIONS`)과 시간대를 확인한다. 서머타임은 `zoneinfo`로 처리한다.
   - 수정치가 있는 경제지표(예: GDP·고용)는 최초 발표치를 별도 보관하지 않으면 학습 피처로 쓰지 않는다. 사용하려면 vintage 데이터가 필요하다.
4. **누락 거래일.**
   - 휴장일 누락과 수집 실패 누락을 구분한다. 긴 누락이 거래소 휴장 일정과 맞는지 확인한다.
   - 누락은 채우지 않는다. 대신 수익률 간격이 길어진다는 영향을 기록한다.
   - 다른 시장과 맞출 때는 반드시 as-of 조인을 쓴다. 날짜 inner join은 휴장일을 미래로 당겨 쓰는 위험이 있다.
5. **테스트.**
   - 파서에는 정상 응답, 결측 표기, HTML 오류 응답, 빈 응답 케이스를 추가한다.
   - 수집기에는 실패 시 이전 이력이 보존되는지와 소스별 실패가 격리되는지를 추가한다.
   - 실행할 명령은 `CLAUDE.md`의 테스트 항목을 따른다.

## 출력 형식
```
## Data Audit — <대상> (<YYYY-MM-DD>)
판정: PASS | PASS WITH WARNINGS | FAIL
| 항목 | 결과 | 근거 |
|---|---|---|
| 라이선스 (수집/저장/ML/재배포) | allowed/…| 약관 URL + 인용 |
| 시점 정합성 | 게시 지연 규칙 | 공식 일정 출처 |
| 누락·지연·이상치 | audit_data.py 요약 | FAIL/WARN 수 |
| 누출 위험 | 없음/있음 | 근거 |
조치: (필요한 코드·문서 변경, 승인 필요 항목)
```

## 실패 시 처리
- **FAIL 발생:** 해당 데이터를 학습이나 표시에 쓰지 않는다. UI와 JSON에는 `unavailable` 또는 `stale`로 표시되는지 확인한다. 데이터를 자동으로 삭제하거나 수정하지 말고, 원인과 복구 방법을 보고한다.
- **약관 페이지를 읽을 수 없음:** 해당 권한을 `unverified`로 기록하고 그 사실을 보고한다.
- **유료 계약이 필요함:** 진행을 멈추고 사용자 승인을 요청한다.
- **스크립트가 exit 2로 끝남(import 실패):** `.venv`로 실행했는지 확인한다.
