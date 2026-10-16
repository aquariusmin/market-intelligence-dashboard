"""Events to watch: FOMC meetings parsed from the Federal Reserve Board calendar page, Bank of Korea monetary
policy meetings (dates registered by hand from the official schedule), plus an optional
user-maintained data/events.local.json ([{"date": "YYYY-MM-DD", "title": "...", "market": "ks", "url": "..."}]).
Nothing is invented: if the calendar cannot be fetched, the section says so.
"""
import json
import re
from datetime import date

import providers

from . import paths

FOMC_URL = 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'
BOK_MPC_URL = 'https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do?mtgSe=A&menuNo=200755'
# 통화정책방향 결정회의, copied by hand from the official page (read 2026-10-16). Dates only; add next year's list
# when the Bank of Korea publishes it (usually in December). Until then the status says it is not registered.
BOK_MPC = {2026: ['2026-01-15', '2026-02-26', '2026-04-10', '2026-05-28', '2026-07-16', '2026-08-27', '2026-10-22',
                  '2026-11-26']}
MONTHS = {m: i for i, m in enumerate(['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
                                      'september', 'october', 'november', 'december'], 1)}


def parse_fomc(html):
    out = []
    heads = list(re.finditer(r'(\d{4}) FOMC Meetings', html))
    for k, h in enumerate(heads):
        year = int(h.group(1))
        seg = html[h.end(): heads[k + 1].start() if k + 1 < len(heads) else len(html)]
        for m in re.finditer(r'fomc-meeting__month[^>]*><strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<',
                             seg, re.S):
            months = [MONTHS.get(_month(x)) for x in m.group(1).split('/')]
            days = re.findall(r'\d+', m.group(2))
            if not days or not months or None in months:
                continue
            month = months[-1]
            last = int(days[-1])
            # A meeting listed under year Y that spans "Dec/Jan" ends in January of Y+1.
            end_year = year + 1 if month < months[0] else year
            try:
                d = date(end_year, month, last)
            except ValueError:
                continue
            out.append({'date': d.isoformat(), 'title': 'FOMC 회의 결과 발표 (%s %s)' % (m.group(1).strip(), m.group(2).strip()),
                        'detail': '* 표시는 경제전망(SEP) 포함 회의' if '*' in m.group(2) else '',
                        'market': 'global', 'source': 'Federal Reserve Board', 'url': FOMC_URL,
                        'kind': 'monetary_policy'})
    return sorted({e['date']: e for e in out}.values(), key=lambda e: e['date'])


def _month(token):
    t = token.strip().lower()
    for name, i in MONTHS.items():
        if name.startswith(t[:3]):
            return name
    return None


def bok_mpc():
    return [{'date': d, 'title': '한국은행 통화정책방향 결정회의 (기준금리 발표)', 'detail': '', 'market': 'kr',
             'source': 'Bank of Korea (공식 일정, 수동 등록)', 'url': BOK_MPC_URL, 'kind': 'monetary_policy'}
            for year in sorted(BOK_MPC) for d in BOK_MPC[year]]


def local_events():
    if not paths.EVENTS_LOCAL.exists():
        return []
    data = json.loads(paths.EVENTS_LOCAL.read_text(encoding='utf-8'))
    return [dict(e, source=e.get('source', '사용자 입력 (events.local.json)'), kind=e.get('kind', 'user'))
            for e in data if isinstance(e, dict) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(e.get('date', '')))]


def upcoming(today=None, request=None, horizon_days=45):
    today = today or date.today()
    request = request or providers.request_text
    status = {'fomc': 'ok', 'error': None}
    try:
        fomc = parse_fomc(request(FOMC_URL))
        if not fomc:
            raise ValueError('FOMC 일정 파싱 결과 없음 (페이지 구조 변경 가능)')
    except Exception as exc:
        fomc, status = [], {'fomc': 'failed', 'error': str(exc)[:300]}
    try:
        user = local_events()
    except (OSError, ValueError) as exc:
        user, status['local_error'] = [], str(exc)
    bok = bok_mpc()
    next_bok = next((e for e in bok if e['date'] >= today.isoformat()), None)
    status['bok_mpc'] = 'ok' if next_bok else 'not_registered'
    if not next_bok:
        status['bok_error'] = '%d년 금통위 일정 미등록 · events.BOK_MPC에 공식 일정 추가 필요' % (max(BOK_MPC) + 1)
    items = [e for e in fomc + bok + user if 0 <= (date.fromisoformat(e['date']) - today).days <= horizon_days]
    return {'items': sorted(items, key=lambda e: e['date']), 'status': status,
            'next_fomc': next((e for e in fomc if e['date'] >= today.isoformat()), None), 'next_bok': next_bok}
