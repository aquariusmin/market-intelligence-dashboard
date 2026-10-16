"""Data-use permission registry and the ML training gate.

Values: 'allowed' | 'conditional' | 'not_allowed' | 'unclear' | 'unverified'.
Only 'allowed' passes the ML gate. Unknown permissions are never assumed to be granted.
'conditional' storage passes only when the entry lists the `conditions` this local, personal,
non-redistributing dashboard satisfies by design; a conditional entry without them is refused.
Verified 2026-10-15 by reading each provider's terms page (summary in README "Data Sources").
"""
import json
import sys

from . import paths

VERIFIED = '2026-10-15'
FRED_TERMS = 'https://fred.stlouisfed.org/legal/'

SOURCES = {
    'fred_sp500': dict(provider='S&P Dow Jones Indices via FRED', dataset='SP500 (daily close)', terms_url=FRED_TERMS,
                       collection='conditional', local_storage='not_allowed', ml_training='not_allowed',
                       redistribution='not_allowed',
                       notes='FRED: AI/ML 개발·훈련 금지, 저장·캐시·보관 금지 조항. S&P: 사전 서면 허가 없는 복제 금지. '
                             '가격 스냅샷(markets.json)은 기존 동작으로 유지되나 약관과 충돌 가능성 있음 (README 참고).'),
    'fred_nasdaq100': dict(provider='Nasdaq, Inc. via FRED', dataset='NASDAQ100 (daily close)', terms_url=FRED_TERMS,
                           collection='conditional', local_storage='not_allowed', ml_training='not_allowed',
                           redistribution='not_allowed', notes='FRED 약관 + "Copyrighted: Pre-Approval Required".'),
    'krx_openapi': dict(provider='Korea Exchange (KRX OPEN API)', dataset='KOSPI / KOSDAQ 일별 지수',
                        terms_url='https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp',
                        collection='allowed', local_storage='conditional', ml_training='allowed',
                        redistribution='not_allowed',
                        conditions=['비상업적 개인 이용 (로컬 대시보드)', '제3자 제공·재배포 없음 (data/ gitignore)',
                                    '계약 종료 시 이용 중단 (사용자 책임)',
                                    '이용 신청 목적에 연구·머신러닝 기재 (사용자 확인 2026-10-15)'],
                        ml_basis='약관에 ML 조항 없음. 사용자가 API 이용 신청 시 목적을 연구·머신러닝으로 기재해 승인받음 '
                                 '(사용자 진술, 2026-10-15). 학습 결과·모델도 제3자에게 제공하지 않음.',
                        notes='비상업적 이용만 허용(제6조②), 제3자 제공 금지(제11조②), 계약 종료 후 이용 불가(제11조③), '
                              '키당 1일 10,000회(제8조④). ML: 신청 목적 기반 허용 (ml_basis).'),
    'fed_h15': dict(provider='Federal Reserve Board (DDP)', dataset='H.15 Treasury constant maturity yields',
                    terms_url='https://www.federalreserve.gov/disclaimer.htm', collection='allowed',
                    local_storage='allowed', ml_training='allowed', redistribution='allowed',
                    notes='"public domain and may be copied and distributed without permission" · 출처 표기 필요.'),
    'fed_h10': dict(provider='Federal Reserve Board (DDP)', dataset='H.10 KRW/USD, Nominal Broad Dollar Index',
                    terms_url='https://www.federalreserve.gov/disclaimer.htm', collection='allowed',
                    local_storage='allowed', ml_training='allowed', redistribution='allowed',
                    notes='Board 공공 도메인 · 주간 게시(월요일) → 최대 약 1주 지연.'),
    'fred_vixcls': dict(provider='Cboe via FRED', dataset='VIXCLS', terms_url=FRED_TERMS, collection='conditional',
                        local_storage='not_allowed', ml_training='not_allowed', redistribution='unclear',
                        notes='기본 비활성. --with-fred-risk 옵트인 시에만 표시용 수집.'),
    'fred_ice_hy': dict(provider='ICE Data Indices via FRED', dataset='BAMLH0A0HYM2 (HY OAS)', terms_url=FRED_TERMS,
                        collection='conditional', local_storage='not_allowed', ml_training='not_allowed',
                        redistribution='not_allowed', notes='ICE: 사전 서면 허가 없는 복제 금지 · 기본 비활성 (옵트인).'),
    'fred_ice_ig': dict(provider='ICE Data Indices via FRED', dataset='BAMLC0A0CM (IG OAS)', terms_url=FRED_TERMS,
                        collection='conditional', local_storage='not_allowed', ml_training='not_allowed',
                        redistribution='not_allowed', notes='ICE: 사전 서면 허가 없는 복제 금지 · 기본 비활성 (옵트인).'),
    'fed_press_rss': dict(provider='Federal Reserve Board', dataset='Press release RSS (title/link/date)',
                          terms_url='https://www.federalreserve.gov/disclaimer.htm', collection='allowed',
                          local_storage='allowed', ml_training='allowed', redistribution='allowed',
                          notes='제3자 자료 제외 공공 도메인.'),
    'sec_press_rss': dict(provider='U.S. SEC', dataset='Press release RSS (title/link/date)',
                          terms_url='https://www.sec.gov/about/developer-resources', collection='conditional',
                          local_storage='allowed', ml_training='allowed', redistribution='allowed',
                          notes='Fair access: 초당 10회 이하, 미분류 봇 금지 → User-Agent 선언. '
                                '개인정보·보안 정책: https://www.sec.gov/privacy.htm#security'),
    'gdelt_doc': dict(provider='GDELT Project', dataset='DOC 2.0 API article list (title/URL/time)',
                      terms_url='https://www.gdeltproject.org/about.html', collection='allowed', local_storage='allowed',
                      ml_training='conditional', redistribution='conditional',
                      notes='GDELT 자체 데이터는 무제한 이용·인용 필요. 기사 제목·URL의 권리는 각 언론사 → 원문 미저장. '
                            '제목 텍스트의 ML 사용은 언론사 권리 미확인 → conditional(학습 불허). '
                            '5초당 1회 제한.'),
    'bok_press_rss': dict(provider='Bank of Korea', dataset='보도자료·통화정책 RSS (title/link/date)',
                          terms_url='https://www.bok.or.kr/portal/main/contents.do?menuNo=200228',
                          collection='not_allowed', local_storage='not_allowed', ml_training='not_allowed',
                          redistribution='not_allowed', verified_on='2026-10-16',
                          notes='저작권보호방침: 공공데이터 목록 외 정보는 직접·딥링크로만 이용, 그 밖의 이용은 사전 승인 필요. '
                                '보도자료 제목도 예외 없음 → 승인 전까지 미수집 (RSS 목록: /static/view/popup/rss_popup.html).'),
    'toss_openapi': dict(provider='Toss Securities Open API', dataset='KOSPI / KOSDAQ 일봉 (당일 종가 잠정치, 시가)',
                         terms_url='https://openapi.tossinvest.com/openapi-docs/faq.md', collection='allowed',
                         local_storage='conditional', ml_training='allowed', redistribution='not_allowed',
                         verified_on='2026-10-16',
                         conditions=['본인 매매 목적의 개인 이용', '제3자 배포 없음 (data/ gitignore)', '상업적 이용 없음'],
                         ml_basis='본인 매매 판단용 개인 모델 → 이용 목적 범위 내 (약관 해석, 2026-10-16)',
                         notes='Toss FAQ 데이터 이용 정책: "투자자 본인의 매매 목적에 한하여 이용", 상업적 이용 및 제3자 배포 금지. '
                              '저장·ML 사용을 금지하는 조항 없음 → 본인 매매 판단용 개인 모델은 목적 범위 내로 판단(2026-10-16). '
                              '토큰에 권한 범위가 없어 주문도 가능 → mi/toss.py가 시세 경로만 허용.'),
    'toss_etf': dict(provider='Toss Securities Open API', dataset='SPY / QQQ 일봉 (S&P 500 · NASDAQ 100 추종 ETF, 분할 수정·배당 미반영, 정규장 시가·종가)',
                     terms_url='https://openapi.tossinvest.com/openapi-docs/faq.md', collection='allowed',
                     local_storage='conditional', ml_training='allowed', redistribution='not_allowed',
                     verified_on='2026-10-16',
                     conditions=['본인 매매 목적의 개인 이용', '제3자 배포 없음 (data/ gitignore)', '상업적 이용 없음'],
                     ml_basis='본인 매매 판단용 개인 모델 → 이용 목적 범위 내 (약관 해석, 2026-10-16)',
                     notes='미국 지수 자체가 아니라 추종 ETF 가격 (일부 거래소 기준, 정규장 공식 종가). ' + 'Toss FAQ 데이터 이용 정책: "투자자 본인의 매매 목적에 한하여 이용", 상업적 이용 및 제3자 배포 금지. '
                              '저장·ML 사용을 금지하는 조항 없음 → 본인 매매 판단용 개인 모델은 목적 범위 내로 판단(2026-10-16). '
                              '토큰에 권한 범위가 없어 주문도 가능 → mi/toss.py가 시세 경로만 허용.'),
    'fomc_calendar': dict(provider='Federal Reserve Board', dataset='FOMC meeting calendar',
                          terms_url='https://www.federalreserve.gov/disclaimer.htm', collection='allowed',
                          local_storage='allowed', ml_training='allowed', redistribution='allowed',
                          notes='Board 웹사이트 공개 일정 · "public domain" (disclaimer.htm) · 출처 표기.'),
}
for _s in SOURCES.values():
    _s.setdefault('verified_on', VERIFIED)

MODELS = {
    'finbert': dict(name='ProsusAI/finbert', license='CC BY-NC-SA 4.0', use='개인·비상업 용도만 · 선택 설치',
                    url='https://huggingface.co/ProsusAI/finbert'),
    'kr_finbert_sc': dict(name='snunlp/KR-FinBert-SC', license='명시 없음', use='라이선스 미확인 → 사용하지 않음',
                          url='https://huggingface.co/snunlp/KR-FinBert-SC'),
}

LICENSED_FIELDS = ('provider', 'dataset', 'terms_url', 'ml_training', 'local_storage', 'verified_on')


def licensed_manifest(path=None):
    """User-supplied licensed data (data/licensed/manifest.json). The user records the licence they hold;
    nothing here creates or assumes one."""
    path = path or paths.LICENSED / 'manifest.json'
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:  # a broken manifest grants nothing but must not stop the pipeline
        print('licensed manifest unreadable (%s): no licensed sources used' % exc, file=sys.stderr)
        return {}
    out = {}
    for market, entry in (data.get('markets') or {}).items():
        missing = [f for f in LICENSED_FIELDS if not str(entry.get(f, '')).strip()]
        out[market] = dict(entry, source_id='licensed:' + market, manifest_errors=missing)
    return out


def source(source_id, manifest=None):
    if source_id.startswith('licensed:'):
        entry = (manifest if manifest is not None else licensed_manifest()).get(source_id.split(':', 1)[1])
        if not entry:
            return None
        return dict(entry, collection='user_supplied', redistribution=entry.get('redistribution', 'unverified'))
    return SOURCES.get(source_id)


def ml_allowed(source_id, manifest=None):
    """(allowed: bool, reason: str)."""
    s = source(source_id, manifest)
    if s is None:
        return False, '%s: 라이선스 기록 없음' % source_id
    if s.get('manifest_errors'):
        return False, '%s: manifest 필수 항목 누락 (%s)' % (source_id, ', '.join(s['manifest_errors']))
    if s.get('ml_training') != 'allowed':
        return False, '%s: ML 학습 권한 %s' % (source_id, s.get('ml_training'))
    if not _storage_ok(s):
        return False, '%s: 로컬 저장 권한 %s' % (source_id, s.get('local_storage'))
    return True, ''


def _storage_ok(s):
    if s.get('local_storage') == 'allowed':
        return True
    # user-supplied manifests record the licence the user holds; built-in conditional entries must list conditions
    return s.get('local_storage') == 'conditional' and (bool(s.get('conditions')) or s.get('collection') == 'user_supplied')


def storage_allowed(source_id, manifest=None):
    s = source(source_id, manifest)
    return bool(s) and _storage_ok(s)


def registry(manifest=None):
    manifest = licensed_manifest() if manifest is None else manifest
    rows = [dict(id=k, **v) for k, v in SOURCES.items()]
    rows += [dict(id=v['source_id'], **{k: v.get(k) for k in LICENSED_FIELDS + ('redistribution',)},
                  collection='user_supplied', notes='사용자 제공 라이선스 데이터 (manifest 기록)')
             for v in manifest.values()]
    return rows
