"""OpenDART disclosure list (metadata only: company, report name, receipt date, viewer link).

Scope: 주요사항보고서 (pblntf_ty=B: rights offerings, mergers, splits, major asset deals ...) of KOSPI and KOSDAQ
companies over the last few days. OpenDART terms (read 2026-10-16) say the service is free with a published call
quota and do not address storing or reusing the returned data, so storage is "unclear": nothing is written to
SQLite; the latest list is shown from news.json and replaced on every run. Not a forecast input.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import providers

from . import timing

API = 'https://opendart.fss.or.kr/api/list.json'
VIEWER = 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='
CLASSES = {'Y': 'ks', 'K': 'kq'}       # corp_cls -> market
DAYS = 3
STATUS_TEXT = {'010': '등록되지 않은 키', '011': '사용할 수 없는 키', '012': '접근 불가 IP', '020': '요청 제한 초과',
               '100': '필드 값 오류', '800': '시스템 점검', '900': '정의되지 않은 오류', '901': '키 만료'}


def configured():
    return bool(os.environ.get('DART_API_KEY', '').strip())


def kind(title):
    """Disclosure type from the report name: '[기재정정]주요사항보고서(회사합병결정)' -> '회사합병'."""
    m = re.search(r'\(([^()]+)\)\s*$', title)
    return re.sub(r'결정$', '', m.group(1)).strip() if m else title


def parse(text, market):
    """-> list of items; raises ValueError on an error status. 013 (no data) is an empty list."""
    try:
        payload = json.loads(text)
    except ValueError:
        raise ValueError('DART JSON 응답 오류') from None
    status = str(payload.get('status', ''))
    if status == '013':
        return []
    if status != '000':
        raise ValueError('DART status %s · %s' % (status, STATUS_TEXT.get(status, payload.get('message', ''))[:80]))
    out = []
    for r in payload.get('list') or []:
        rcept, name, title, day = (str(r.get(k) or '').strip() for k in ('rcept_no', 'corp_name', 'report_nm', 'rcept_dt'))
        if not (rcept.isdigit() and len(day) == 8 and name and title):
            continue
        out.append({'date': '%s-%s-%s' % (day[:4], day[4:6], day[6:]), 'company': name[:100], 'report': title[:200],
                    'stock_code': str(r.get('stock_code') or '')[:6], 'market': market,
                    'kind': kind(title), 'amended': title.startswith('['), 'url': VIEWER + rcept, 'rcept_no': rcept})
    return out


def collect(request=None, now=None):
    """Latest major-matter reports for KOSPI and KOSDAQ companies. Never raises; one market failing keeps the other."""
    now = now or datetime.now(timezone.utc)
    rep = {'status': 'unavailable', 'items': [], 'error': None, 'fetched_at': now.isoformat(timespec='seconds'),
           'scope': '주요사항보고서 · KOSPI·KOSDAQ · 최근 %d일' % DAYS, 'source': 'OpenDART (금융감독원)',
           'storage': '보관하지 않음 (약관에 저장 조항 없음 → unclear) · 매 실행 교체'}
    if not configured():
        rep['error'] = 'DART_API_KEY 없음 · ~/.config/mi/env 확인'
        return rep
    rep['truncated'] = []
    request = request or providers.request_text
    today = now.astimezone(timing.KST).date()
    errors = []
    for cls, market in CLASSES.items():
        q = {'crtfc_key': os.environ['DART_API_KEY'].strip(), 'bgn_de': (today - timedelta(days=DAYS)).strftime('%Y%m%d'),
             'end_de': today.strftime('%Y%m%d'), 'pblntf_ty': 'B', 'corp_cls': cls, 'last_reprt_at': 'Y',
             'page_count': 100}
        try:   # the key travels in the URL, which request_text passes through curl stdin config (never argv)
            text = request(API + '?' + urlencode(q))
            got = parse(text, market)
            total = int(json.loads(text).get('total_count') or len(got))
            if total > q['page_count']:   # one page per market: say so instead of silently dropping the rest
                rep['truncated'].append('%s %d건 중 최신 %d건' % (market, total, q['page_count']))
            rep['items'] += got
        except Exception as exc:
            errors.append('%s: %s' % (market, str(exc).replace(q['crtfc_key'], '***')[:200]))
    rep['items'].sort(key=lambda i: (i['date'], i['rcept_no']), reverse=True)
    rep['error'] = ' · '.join(errors) or None
    rep['status'] = 'failed' if len(errors) == len(CLASSES) else ('partial' if errors else 'ok')
    return rep
