---
name: dashboard-qa
description: QA the local dashboard after UI or JSON-contract changes — REAL vs DEMO separation, every panel shows source/as-of/status, unavailable and not-validated states, console errors, mobile (390px) layout, chart range/market/horizon controls, XSS-safe rendering of external text. Use when editing index.html, app.js, or the shape of data/dashboard/*.json written by mi_cli.py.
---

# Dashboard QA

공통 원칙은 `CLAUDE.md` 원칙 1·6을 따른다. 기존 디자인은 그대로 유지한다. 새 시각 방향이 필요할 때만 `frontend-design`을 쓰고, 상호작용 디버깅에는 `playwright-cli` Skill을 쓴다.

## 실행 조건
- 화면 파일 3개 중 하나를 바꾸었거나, `mi_cli.py`가 쓰는 JSON 키를 추가·변경·삭제했을 때
- 대상이 아님: 엔진 로직만 바꾸고 JSON 형태는 그대로인 경우. 이때는 단위 테스트로 충분하다.

## 절차
1. **정적 검사와 JS 테스트.** `node --check app.js && node scripts/test_ui.js && node --test scripts/test_intel.mjs`
2. **계약 확인.** JSON 키를 바꿨다면 해당 키를 쓰는 모든 곳을 grep으로 찾는다(`app.js`, `briefing.py`, `mi_cli.py`). 파일이 없거나 키가 빠졌을 때 화면이 "파일 없음 / 이용 불가"를 보여주고 예외가 나지 않는지 확인한다.
3. **실데이터 스모크.**
   ```sh
   MI_CHROMIUM=~/.cache/ms-playwright/chromium_headless_shell-1247/chrome-headless-shell-linux64/chrome-headless-shell \
     .venv/bin/python scripts/ui_smoke.py --shots <scratch>/shots --expect "Forecast=UNAVAILABLE"
   ```
   검사 범위는 REAL·DEMO × 6탭 × sp/ks × 1D/5D/20D × 데스크톱/390px이며, 콘솔 오류, 가로 넘침, 빈 화면을 잡는다.
4. **채워진 상태 스모크(패널 렌더링 변경 시).**
   - 스크래치 디렉터리에 합성 픽스처를 만들고 `MI_DATA_DIR=<fx>/data`로 파이프라인을 실행한 뒤 `ui_smoke.py --root <fx>`를 돌린다.
   - 픽스처 데이터는 `data/`에 복사하지 않는다.
   - `--expect "Model Performance=Confusion Matrix"`처럼 핵심 패널을 지정한다.
5. **스크린샷 확인.** 바뀐 탭의 데스크톱과 phone 스크린샷을 Read로 열어 아래를 확인한다.
   - 정렬과 겹침
   - 숫자 단위(%, %p, bp, KRW)와 자릿수
   - 상승·하락 색 의미
   - VALIDATED / NOT VALIDATED / UNAVAILABLE 배지
6. **표시 규칙 체크리스트.**
   - 모든 패널에 출처(source_id 또는 공급자 링크)와 기준시각(as_of / generated_at)이 있는가
   - REAL 화면에 DEMO 값이 섞이지 않는가. DEMO 화면은 SAMPLE 표시를 유지하는가
   - 미검증 확률은 흐리게 표시하고 경고와 사유를 함께 보여주는가. 라이선스로 막힌 경우 UNAVAILABLE과 사유를 보여주는가
   - 백테스트와 실시간 예측 이력이 분리되어 있는가
   - 외부 문자열(뉴스 제목, 오류 메시지)은 모두 `esc()`를 거치는가. 링크는 `safeUrl()`로 http(s)만 허용하는가
   - `file://`로 열었을 때 DEMO는 동작하고 REAL은 서버 안내를 표시하는가

## 출력 형식
```
## Dashboard QA — <변경 요약>
판정: PASS | FAIL
- node --check: OK/…
- ui_smoke (real): N views, OK / 문제 목록
- ui_smoke (fixture): N views, OK / 문제 목록 (해당 시)
- 스크린샷 검토: 탭별 1줄 (문제 있으면 위치·내용)
- 표시 규칙 체크리스트: 위반 항목만 나열
```

## 실패 시 처리
- **콘솔 오류 또는 빈 화면:** 원인 파일과 줄을 찾아 수정한 뒤 다시 실행한다.
- **필수 JSON이 없어 생긴 404:** 정상 상태일 수 있다(해당 단계 미실행). 화면이 "파일 없음"을 안내하는지만 확인한다.
- **브라우저 실행 실패:** 캐시된 chromium 경로를 확인한다. 대체 수단이 없으면 브라우저 검증을 하지 못했다고 명시한다. 이 경우 통과로 보고하지 않는다.
- **DEMO가 손상됨:** 최우선으로 원복한다(`index.html` DEMO 함수는 변경 금지 대상).
