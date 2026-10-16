"""Owner-only web copy of the dashboard (abovemin.com/lab/markets).

The bundle is uploaded to an endpoint of the owner's site, which stores it in a private Supabase bucket and shows it
only to the signed-in owner. Uploading is storing on third-party infrastructure, so only sources whose terms allow
local storage go in (licenses.storage_allowed):
- price series: KRX (+ Toss provisional closes) for KOSPI/KOSDAQ; for S&P 500 / NASDAQ 100 the FRED index series may
  not be stored, so the tracking ETFs (SPY/QQQ via Toss) are used and labelled as such;
- regimes are recomputed here on exactly those series with archived macro only (Fed H.15/H.10), never the opt-in
  FRED risk snapshot, so no FRED-derived number is uploaded;
- the briefing is the restricted copy archived in SQLite (no numbers from storage-restricted sources);
- the reference macro file keeps ECOS entries only.
The site never redistributes it: owner-only page, noindex, private bucket. Not a public feed.
"""
import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone

from . import VERSION, daily, licenses, macro, paths, prices, regime, store

SCHEMA = 'mi-web/1'
CHART_YEARS = 5
MAX_BODY = 4_000_000          # Vercel function request limit is 4.5 MB


class PublishError(RuntimeError):
    pass


def _read(name):
    p = paths.DASHBOARD / name
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else None


def _market(conn, key, snap, mrows, manifest):
    s = prices.series(conn, key, snap, manifest)
    proxy = None
    if not licenses.storage_allowed(s['source_id'], manifest):
        s = prices.target_series(conn, key, snap, manifest)
        proxy = s.get('proxy')
    name = prices.MARKETS[key]['name']
    if not s['rows'] or not licenses.storage_allowed(s['source_id'], manifest):
        return {'name': name, 'status': 'unavailable',
                'reason': '저장 가능한 가격 출처 없음 (%s)' % s.get('source_id')}
    rows = s['rows']
    r = regime.analyze(key, rows, mrows)
    r['daily'] = daily.market_block(r)
    cutoff = (datetime.fromisoformat(rows[-1]['date']) - timedelta(days=365 * CHART_YEARS)).date().isoformat()
    from collect_data import indicators   # same formulas as the local cards
    return {'name': name, 'label': '%s (%s 기준)' % (name, proxy) if proxy else name, 'proxy': proxy,
            'status': 'ok', 'source_id': s['source_id'], 'as_of': rows[-1]['date'],
            'provisional': s.get('provisional') or [], 'indicators': indicators(rows),
            'rows': [[x['date'], x['close']] for x in rows if x['date'] >= cutoff], 'regime': r}


def _latest_briefing(conn):
    row = conn.execute('SELECT content FROM briefings ORDER BY id DESC LIMIT 1').fetchone()
    return json.loads(row['content']) if row else None


def _reference_macro():
    p = paths.DATA / 'reference_only' / 'macro.json'
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding='utf-8'))
    d['indicators'] = {k: v for k, v in (d.get('indicators') or {}).items() if v.get('provider') == 'ecos'}
    return d


def build(conn, now=None):
    """-> {name: payload} ready to upload. Raises PublishError if a storage-restricted source slipped in."""
    now = now or datetime.now(timezone.utc)
    manifest = licenses.licensed_manifest()
    snap = prices.read_snapshot()
    mrows = {sid: store.load_macro(conn, sid) for sid in macro.SERIES}   # archived (storage-permitted) only
    markets = {k: _market(conn, k, snap, mrows, manifest) for k in prices.MARKETS}
    stamp = now.isoformat(timespec='seconds')
    core = {'schema': SCHEMA, 'generated_at': stamp, 'engine_version': VERSION,
            'notice': '본인 전용 · 재배포 금지 (KRX·토스증권 약관) · S&P 500/NASDAQ 100은 SPY/QQQ 기준 (FRED 저장 불가)',
            'markets': markets, 'macro': (_read('macro.json') or {}).get('series'),
            'reference_macro': _reference_macro(), 'briefing': _latest_briefing(conn),
            'forecast': _read('forecast.json'), 'status': _read('status.json')}
    files = {'core': core, 'news': _read('news.json'), 'performance': _read('performance.json')}
    check(files)
    return files


def check(files):
    """Fail closed: every market series in the bundle must come from a storage-permitted source."""
    for key, m in (files['core'].get('markets') or {}).items():
        if m.get('status') == 'ok' and not licenses.storage_allowed(m['source_id']):
            raise PublishError('%s: 저장 불가 출처 %s' % (key, m['source_id']))
    if 'fred_risk' in json.dumps(files['core'].get('macro') or {}):
        raise PublishError('FRED 위험 지표 스냅샷이 묶음에 포함됨')


def upload(files, url, token, run=subprocess.run):
    """POST the bundle to the owner's site. The token goes through curl's stdin config, never argv or logs;
    the body is written to a 0600 temp file under data/ and removed afterwards."""
    if not url.startswith('https://'):
        raise PublishError('MI_PUBLISH_URL은 https만 허용')
    if not token or any(c in token for c in '\r\n"\\'):
        raise PublishError('MI_PUBLISH_TOKEN 없음 또는 형식 오류')
    body = json.dumps({'schema': SCHEMA, 'files': files}, ensure_ascii=False, separators=(',', ':')).encode()
    if len(body) > MAX_BODY:
        raise PublishError('묶음 크기 %d bytes > %d' % (len(body), MAX_BODY))
    fd, tmp = tempfile.mkstemp(prefix='.publish-', suffix='.json', dir=paths.DATA)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(body)
        config = ('url = "%s"\nheader = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\n'
                  'data-binary = "@%s"\n' % (url, token, tmp))
        marker = '\n__MI_HTTP_STATUS__:'
        res = run(['curl', '--silent', '--show-error', '--proto', '=https', '--max-time', '60', '--config', '-',
                   '--write-out', marker + '%{http_code}'], input=config, text=True, capture_output=True, timeout=75)
    finally:
        os.unlink(tmp)
    out, _, status = res.stdout.rpartition(marker)
    status = status.strip()
    if res.returncode or not status.startswith('2'):
        raise PublishError('업로드 실패 HTTP %s %s' % (status or '000', (out or res.stderr)[:200].replace(token, '***')))
    return {'status': int(status), 'bytes': len(body), 'response': out[:300]}
