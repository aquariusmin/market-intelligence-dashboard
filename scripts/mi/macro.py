"""Macro / risk series collectors. Each series maps to a licence entry in licenses.py.

No interpolation: 'ND' / '.' / blank are skipped, never filled.
"""
import csv
import io
import math
import time
from datetime import date, timedelta
from urllib.parse import urlencode

import providers  # scripts/providers.py: curl wrapper with retries, TLS verification

from . import licenses, store

DDP = 'https://www.federalreserve.gov/datadownload/Output.aspx'
SERIES = {
    'UST3M': {'name': '미국 3개월 국채금리', 'unit': '%', 'source_id': 'fed_h15', 'kind': 'ddp', 'rel': 'H15',
              'package': 'bf17364827e38702b42a58cf8eaa3f78', 'column': 'RIFLGFCM03_N.B', 'range': (-5, 30),
              'lag': 'next_business_day_17et'},
    'UST2Y': {'name': '미국 2년 국채금리', 'unit': '%', 'source_id': 'fed_h15', 'kind': 'ddp', 'rel': 'H15',
              'package': 'bf17364827e38702b42a58cf8eaa3f78', 'column': 'RIFLGFCY02_N.B', 'range': (-5, 30),
              'lag': 'next_business_day_17et'},
    'UST10Y': {'name': '미국 10년 국채금리', 'unit': '%', 'source_id': 'fed_h15', 'kind': 'ddp', 'rel': 'H15',
               'package': 'bf17364827e38702b42a58cf8eaa3f78', 'column': 'RIFLGFCY10_N.B', 'range': (-5, 30),
               'lag': 'next_business_day_17et'},
    'USDKRW': {'name': 'USD/KRW (뉴욕 정오 매입률)', 'unit': 'KRW', 'source_id': 'fed_h10', 'kind': 'ddp', 'rel': 'H10',
               'package': '60f32914ab61dfab590e0e470153e3ae', 'column': 'RXI_N.B.KO', 'range': (300, 5000),
               'lag': 'weekly_h10'},
    'USD_BROAD': {'name': '명목 광의 달러지수', 'unit': 'index', 'source_id': 'fed_h10', 'kind': 'ddp', 'rel': 'H10',
                  'package': '122e3bcb627e8e53f1bf72a1a09cfb81', 'column': 'JRXWTFB_N.B', 'range': (40, 250),
                  'lag': 'weekly_h10'},
    'VIX': {'name': 'VIX', 'unit': 'index', 'source_id': 'fred_vixcls', 'kind': 'fred', 'fred_id': 'VIXCLS',
            'range': (3, 200), 'lag': 'next_day_18et'},
    'HY_OAS': {'name': '미국 하이일드 스프레드 (ICE BofA OAS)', 'unit': '%p', 'source_id': 'fred_ice_hy', 'kind': 'fred',
               'fred_id': 'BAMLH0A0HYM2', 'range': (0, 40), 'lag': 'next_day_18et'},
    'IG_OAS': {'name': '미국 투자등급 스프레드 (ICE BofA OAS)', 'unit': '%p', 'source_id': 'fred_ice_ig', 'kind': 'fred',
               'fred_id': 'BAMLC0A0CM', 'range': (0, 15), 'lag': 'next_day_18et'},
}


def parse_ddp(text, column):
    """Fed DDP 'seriescolumn' CSV: 5 label rows, then a header row starting 'Time Period'."""
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith('"Time Period"') or l.startswith('Time Period'))
    except StopIteration:
        raise ValueError('DDP CSV 형식 불일치 (Time Period 헤더 없음)') from None
    reader = csv.reader(io.StringIO('\n'.join(lines[start:])))
    header = next(reader)
    if column not in header:
        raise ValueError('DDP CSV에 %s 열 없음' % column)
    j = header.index(column)
    rows = []
    for rec in reader:
        if len(rec) <= j or rec[j] in ('', 'ND', 'NA', 'NC'):
            continue
        rows.append((date.fromisoformat(rec[0]).isoformat(), float(rec[j])))
    return rows


def parse_fred(text, fred_id):
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or 'observation_date' not in reader.fieldnames or fred_id not in reader.fieldnames:
        raise ValueError('FRED CSV 형식 불일치')
    return [(r['observation_date'], float(r[fred_id])) for r in reader if r[fred_id] not in ('', '.')]


def validate(rows, spec, today):
    lo, hi = spec['range']
    last = ''
    for d, v in rows:
        if d <= last or date.fromisoformat(d) > today or not math.isfinite(v) or not lo <= v <= hi:
            raise ValueError('%s 검증 실패 (%s, %s)' % (spec['name'], d, v))
        last = d
    if not rows:
        raise ValueError('%s 관측값 없음' % spec['name'])
    return rows


def _url(spec, start, today):
    if spec['kind'] == 'ddp':
        return DDP + '?' + urlencode({'rel': spec['rel'], 'series': spec['package'], 'lastobs': '',
                                      'from': start.strftime('%m/%d/%Y'), 'to': today.strftime('%m/%d/%Y'),
                                      'filetype': 'csv', 'label': 'include', 'layout': 'seriescolumn',
                                      'type': 'package'})
    return 'https://fred.stlouisfed.org/graph/fredgraph.csv?' + urlencode(
        {'id': spec['fred_id'], 'cosd': start.isoformat(), 'coed': today.isoformat()})


def _parse(text, spec, start, today):
    if not text.strip():
        raise ValueError('빈 응답 (일시적 제한 가능 · 잠시 후 재시도)')
    rows = parse_ddp(text, spec['column']) if spec['kind'] == 'ddp' else parse_fred(text, spec['fred_id'])
    return validate([r for r in rows if r[0] >= start.isoformat()], spec, today)


def _fetch_chunked(spec, start, today, request, sleep, years=5, attempts=4):
    """DDP intermittently returns an empty body under load: fetch <=5-year windows, retry empties.

    Returns {column: rows} for every column of the package (FRED: raw CSV text)."""
    if spec['kind'] != 'ddp':
        return request(_url(spec, start, today))
    merged, lo = {}, start
    while lo <= today:
        hi = min(date(lo.year + years, 1, 1) - timedelta(days=1), today)
        for attempt in range(attempts):
            text = request(_url(spec, lo, hi))
            if text.strip():
                break
            if attempt == attempts - 1:
                raise ValueError('DDP 빈 응답 반복 (%s~%s) · 잠시 후 재시도' % (lo, hi))
            sleep(3 * (attempt + 1))
        for column in _columns(text):
            merged.setdefault(column, []).extend(parse_ddp(text, column))
        lo = hi + timedelta(days=1)
    return merged


def _columns(text):
    for line in text.splitlines():
        if line.startswith('"Time Period"') or line.startswith('Time Period'):
            return [c for c in next(csv.reader([line]))[1:]]
    raise ValueError('DDP CSV 형식 불일치 (Time Period 헤더 없음)')


def collect(conn, today=None, request=None, selected=None, with_fred_risk=False,
            first_start=date(2000, 1, 3), log=print, sleep=time.sleep):
    """Archive storage-permitted series incrementally (re-request 30 days back to capture revisions).

    Series whose terms forbid local storage (FRED-hosted Cboe/ICE) are skipped unless the user opts in
    with with_fred_risk=True; then they are fetched for display only and returned in `transient`,
    never written to SQLite.
    """
    today = today or date.today()
    results, transient, cache = {}, {}, {}
    base = request or providers.request_text
    for sid, spec in SERIES.items():
        if selected and sid not in selected:
            continue
        archived = licenses.storage_allowed(spec['source_id'])
        if not archived and not with_fred_risk:
            results[sid] = {'status': 'disabled', 'as_of': None,
                            'error': '약관상 로컬 저장 불가 · 기본 비활성 (--with-fred-risk 옵트인)'}
            continue
        existing = store.load_macro(conn, sid) if archived else []
        start = (date.fromisoformat(existing[-1][0]) - timedelta(days=30) if existing
                 else first_start if archived else today - timedelta(days=3 * 366))
        try:
            key = (spec.get('package') or spec.get('fred_id'), start)
            if key not in cache:
                cache[key] = _fetch_chunked(spec, start, today, base, sleep)
            rows = cache[key] if spec['kind'] == 'ddp' else _parse(cache[key], spec, start, today)
            if spec['kind'] == 'ddp':
                rows = validate([r for r in rows[spec['column']] if r[0] >= start.isoformat()], spec, today)
            if archived:
                ins, rev = store.upsert_macro(conn, sid, rows, spec['source_id'])
                results[sid] = {'status': 'ok', 'as_of': rows[-1][0], 'inserted': ins, 'revised': rev, 'error': None}
                log('%s: OK · %s · +%d (rev %d)' % (sid, rows[-1][0], ins, rev))
            else:
                transient[sid] = rows
                results[sid] = {'status': 'ok_transient', 'as_of': rows[-1][0], 'error': None}
                log('%s: OK (표시 전용, 미보관) · %s' % (sid, rows[-1][0]))
        except Exception as exc:
            cache.pop(key, None)
            results[sid] = {'status': 'stale' if existing else 'unavailable',
                            'as_of': existing[-1][0] if existing else None, 'error': str(exc)}
            log('%s: %s · %s' % (sid, results[sid]['status'], exc))
    return results, transient
