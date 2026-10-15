"""Data-use permission registry and the ML training gate.

Values: 'allowed' | 'conditional' | 'not_allowed' | 'unclear' | 'unverified'.
Only 'allowed' passes the ML gate. Unknown permissions are never assumed to be granted.
Verified 2026-10-15 by reading each provider's terms page (summary in README "Data Sources").
"""
import json

from . import paths

VERIFIED = '2026-10-15'
FRED_TERMS = 'https://fred.stlouisfed.org/legal/'

SOURCES = {
    'fred_sp500': dict(provider='S&P Dow Jones Indices via FRED', dataset='SP500 (daily close)', terms_url=FRED_TERMS,
                       collection='conditional', local_storage='not_allowed', ml_training='not_allowed',
                       redistribution='not_allowed',
                       notes='FRED: AI/ML 개발·훈련 금지, 저장·캐시·보관 금지 조항. S&P: 사전 서면 허가 없는 복제 금지. '
                             'v0.2의 markets.json 스냅샷은 기존 동작으로 유지되나 약관과 충돌 가능성 있음 (README 참고).'),
    'fred_nasdaq100': dict(provider='Nasdaq, Inc. via FRED', dataset='NASDAQ100 (daily close)', terms_url=FRED_TERMS,
                           collection='conditional', local_storage='not_allowed', ml_training='not_allowed',
                           redistribution='not_allowed', notes='FRED 약관 + "Copyrighted: Pre-Approval Required".'),
    'krx_openapi': dict(provider='Korea Exchange (KRX OPEN API)', dataset='KOSPI / KOSDAQ 일별 지수',
                        terms_url='https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp',
                        collection='allowed', local_storage='conditional', ml_training='unclear',
                        redistribution='not_allowed',
                        notes='비상업적 이용만 허용(제6조②), 제3자 제공 금지(제11조②), 계약 종료 후 이용 불가(제11조③). '
                              'ML 사용 명시 없음 → 학습 불허로 취급.'),
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
                          terms_url='https://www.sec.gov/privacy', collection='conditional', local_storage='allowed',
                          ml_training='allowed', redistribution='allowed',
                          notes='User-Agent 선언, 초당 10회 이하 (fair access).'),
    'gdelt_doc': dict(provider='GDELT Project', dataset='DOC 2.0 API article list (title/URL/time)',
                      terms_url='https://www.gdeltproject.org/about.html', collection='allowed', local_storage='allowed',
                      ml_training='allowed', redistribution='conditional',
                      notes='GDELT 자체 데이터는 무제한 이용·인용 필요. 기사 제목·URL의 권리는 각 언론사 → 원문 미저장. '
                            '5초당 1회 제한.'),
    'fomc_calendar': dict(provider='Federal Reserve Board', dataset='FOMC meeting calendar',
                          terms_url='https://www.federalreserve.gov/disclaimer.htm', collection='allowed',
                          local_storage='allowed', ml_training='allowed', redistribution='allowed', notes=''),
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
    data = json.loads(path.read_text(encoding='utf-8'))
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
    if s.get('local_storage') not in ('allowed', 'conditional'):
        return False, '%s: 로컬 저장 권한 %s' % (source_id, s.get('local_storage'))
    return True, ''


def storage_allowed(source_id, manifest=None):
    s = source(source_id, manifest)
    return bool(s) and s.get('local_storage') in ('allowed', 'conditional')


def registry(manifest=None):
    manifest = licensed_manifest() if manifest is None else manifest
    rows = [dict(id=k, **v) for k, v in SOURCES.items()]
    rows += [dict(id=v['source_id'], **{k: v.get(k) for k in LICENSED_FIELDS + ('redistribution',)},
                  collection='user_supplied', notes='사용자 제공 라이선스 데이터 (manifest 기록)')
             for v in manifest.values()]
    return rows
