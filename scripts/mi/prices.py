"""Price series access with provenance.

Priority per market: user-licensed CSV (data/licensed) > archived KRX rows + markets.json > markets.json.
FRED rows are read from the price snapshot (markets.json) only; they are never copied into the SQLite archive.
"""
import csv
import json
import math
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import licenses, paths, store

MARKETS = {
    'sp': {'name': 'S&P 500', 'snapshot_source': 'fred_sp500', 'region': 'US'},
    'nq': {'name': 'NASDAQ 100', 'snapshot_source': 'fred_nasdaq100', 'region': 'US'},
    'ks': {'name': 'KOSPI', 'snapshot_source': 'krx_openapi', 'region': 'KR'},
    'kq': {'name': 'KOSDAQ', 'snapshot_source': 'krx_openapi', 'region': 'KR'},
}


def read_snapshot(path=None):
    path = path or paths.MARKETS_JSON
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or not isinstance(data.get('markets'), dict):
        raise ValueError('markets.json 스키마 오류')
    return data


def validate_rows(rows, today=None):
    today = today or datetime.now(ZoneInfo('Asia/Seoul')).date()  # furthest-ahead market clock
    out, last = [], ''
    for r in rows:
        d, c = str(r['date']), r['close']
        if d <= last or date.fromisoformat(d) > today or isinstance(c, bool) or not isinstance(c, (int, float)) \
                or not math.isfinite(c) or c <= 0:
            raise ValueError('가격 행 검증 실패: %s' % d)
        out.append({'date': d, 'close': float(c)})
        last = d
    return out


def load_licensed_csv(market, manifest, today=None):
    entry = manifest.get(market)
    if not entry:
        return None
    path = paths.LICENSED / entry.get('file', market + '.csv')
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not {'date', 'close'} <= set(reader.fieldnames):
            raise ValueError('%s: date,close 헤더 필요' % path.name)
        rows = [{'date': r['date'].strip(), 'close': float(r['close'])} for r in reader if r['close'].strip()]
    return validate_rows(sorted(rows, key=lambda r: r['date']), today)


def ingest_snapshot(conn, snapshot=None):
    """Archive snapshot rows whose source permits local storage (KRX today; never FRED)."""
    snapshot = snapshot if snapshot is not None else read_snapshot()
    report = {}
    for key, cfg in MARKETS.items():
        m = (snapshot or {}).get('markets', {}).get(key) or {}
        sid = cfg['snapshot_source']
        if not licenses.storage_allowed(sid):
            report[key] = 'not_archived (%s: 로컬 보관 불가)' % sid
            continue
        rows = m.get('history') or []
        if not rows:
            report[key] = 'no_rows'
            continue
        ins, rev = store.upsert_prices(conn, key, [(r['date'], r['close']) for r in rows], sid)
        report[key] = 'archived +%d (rev %d)' % (ins, rev)
    return report


ETF_PROXY = {'sp': 'SPY', 'nq': 'QQQ'}
PROVISIONAL_NOTE = '최신 종가 잠정치 (토스증권) · KRX 공식값은 다음 날 확정'


def latest_overlay(conn, snapshot, now_iso):
    """Snapshot-shaped entries for markets whose newest close is a provisional (Toss) row the snapshot lacks.
    History = snapshot history + provisional rows; indicators use the collector formulas. FRED markets never appear."""
    from collect_data import indicators   # collector formulas, so cards and the Indicators tab stay consistent
    out = {}
    for key in MARKETS:
        base = (snapshot or {}).get('markets', {}).get(key) or {}
        s = series(conn, key, snapshot, {})
        new = [r for r in s['rows'] if r['date'] in set(s.get('provisional') or []) and r['date'] > (base.get('as_of') or '')]
        if not new or not base.get('history'):
            continue
        hist = [{'date': r['date'], 'close': r['close']} for r in base['history']] + new
        out[key] = dict(base, history=hist, value=hist[-1]['close'], as_of=hist[-1]['date'],
                        change_pct=(hist[-1]['close'] / hist[-2]['close'] - 1) * 100, indicators=indicators(hist),
                        updated_at=now_iso, provisional={'dates': [r['date'] for r in new], 'note': PROVISIONAL_NOTE})
    return out


def target_series(conn, market, snapshot=None, manifest=None):
    """Price series a model is trained on and scored against. Same as series(), except for US markets whose
    index feed may not be used for ML: there the tracking ETF (SPY/QQQ via Toss) is the target, if archived."""
    manifest = licenses.licensed_manifest() if manifest is None else manifest
    if market in ETF_PROXY and market not in manifest:
        rows = store.load_prices(conn, market, 'toss_etf')
        if rows:
            return {'rows': [{'date': r['date'], 'close': r['close']} for r in rows], 'source_id': 'toss_etf',
                    'origin': 'Toss %s (ETF proxy)' % ETF_PROXY[market], 'proxy': ETF_PROXY[market], 'status': 'ok',
                    'as_of': rows[-1]['date'], 'error': None, 'provisional': []}
    return series(conn, market, snapshot, manifest)


OPEN_SOURCE = {'toss_etf': 'toss_etf', 'krx_openapi': 'toss_openapi'}   # target close source -> open source


def target_opens(conn, market, target):
    """Opening prices keyed by date for a target series, from the matching Toss feed; None if there is no such feed
    (licensed CSV, FRED snapshot). Used only to score next-open entry after the fact, never as a feature."""
    src = OPEN_SOURCE.get(target.get('source_id'))
    return store.load_opens(conn, market, src) if src else None


def series(conn, market, snapshot=None, manifest=None):
    """-> dict(rows, source_id, origin, status, error)."""
    manifest = licenses.licensed_manifest() if manifest is None else manifest
    if market in manifest:
        try:
            rows = load_licensed_csv(market, manifest)
            return {'rows': rows, 'source_id': 'licensed:' + market, 'origin': 'data/licensed', 'status': 'ok',
                    'error': None}
        except (OSError, ValueError) as exc:
            return {'rows': [], 'source_id': 'licensed:' + market, 'origin': 'data/licensed', 'status': 'unavailable',
                    'error': str(exc)}
    snapshot = snapshot if snapshot is not None else read_snapshot()
    cfg = MARKETS[market]
    m = (snapshot or {}).get('markets', {}).get(market) or {}
    rows = {r['date']: r['close'] for r in m.get('history') or []}
    if licenses.storage_allowed(cfg['snapshot_source']):
        for r in store.load_prices(conn, market, cfg['snapshot_source']):
            rows.setdefault(r['date'], r['close'])
    provisional = []
    if licenses.storage_allowed(cfg['snapshot_source']) and rows:
        # same KRX index from Toss for sessions KRX has not published yet (next-morning) -> same-day close
        last = max(rows)
        for r in store.load_prices(conn, market, 'toss_openapi'):
            if r['date'] > last:
                rows[r['date']] = r['close']
                provisional.append(r['date'])
    merged = [{'date': d, 'close': rows[d]} for d in sorted(rows)]
    return {'rows': merged, 'source_id': cfg['snapshot_source'], 'origin': 'markets.json', 'provisional': provisional,
            'status': m.get('status', 'unavailable') if merged else 'unavailable',
            'as_of': merged[-1]['date'] if merged else None, 'error': m.get('error')}
