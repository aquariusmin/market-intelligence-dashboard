"""v0.5 KRX daily index history backfill into the SQLite archive (ROADMAP 0.5-B).

- Resumable: dates already archived, and past dates KRX answered with no data (holidays), are skipped.
  Every answer is recorded in `krx_calendar`, so the observed trading calendar accumulates as a by-product.
- Rate-limited: a fixed pause between calls and a per-day call budget well under the KRX limit
  (terms Art. 8(4): 10,000 requests per key per day), counted across runs in `provider_calls`.
- Stops at the first provider error (auth, network, format); everything fetched so far is kept.
- Only weekdays up to yesterday (KST) are requested: KRX publishes a session's data the next morning.
The v0.2 collector and markets.json are not touched; prices.series() merges the archive with the snapshot.
"""
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import store

SOURCE_ID = 'krx_openapi'
DAILY_LIMIT = 10000          # KRX terms, per key per day
DAILY_BUDGET = 8000          # this tool's share; the rest stays free for daily ingest and manual checks
INTERVAL_S = 0.2             # pause between calls
RETRY_WAITS = (5, 30)        # transient answers (non-JSON page, 5xx, timeout): wait and ask again
RECHECK_DAYS = 7             # a recent "no data" answer may be a not-yet-published day: ask again
KST = ZoneInfo('Asia/Seoul')
CODES = {'ks': 'KOSPI', 'kq': 'KOSDAQ'}


def _transient(exc):
    text = str(exc)
    return not any(k in text for k in ('HTTP 401', 'HTTP 403', 'KRX_API_KEY', '인증키', 'OutBlock', '기준일 불일치',
                                         '대상 대표 지수'))


def _fetch_with_retry(fetch, cfg, day, sleep):
    """-> (row, calls_made). Auth / format errors raise at once; transient ones are retried."""
    for i, wait in enumerate((0,) + RETRY_WAITS):
        if wait:
            sleep(wait)
        try:
            return fetch(cfg, day), i + 1
        except Exception as exc:
            if i == len(RETRY_WAITS) or not _transient(exc):
                exc.calls_made = i + 1
                raise


def kst_today(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(KST).date()


def calls_today(conn, day):
    row = conn.execute("SELECT calls FROM provider_calls WHERE provider='krx' AND day_kst=?", (day.isoformat(),)).fetchone()
    return row[0] if row else 0


def _count_call(conn, day):
    conn.execute("INSERT INTO provider_calls (provider, day_kst, calls) VALUES ('krx', ?, 1) "
                 "ON CONFLICT(provider, day_kst) DO UPDATE SET calls = calls + 1", (day.isoformat(),))


def pending_dates(conn, market, start, end, today):
    have = {r[0] for r in conn.execute('SELECT date FROM prices WHERE market=? AND source_id=?', (market, SOURCE_ID))}
    closed = {r[0] for r in conn.execute(
        "SELECT date FROM krx_calendar WHERE market=? AND status='no_data' AND date < ?",
        (market, (today - timedelta(days=RECHECK_DAYS)).isoformat()))}
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d.isoformat() not in have and d.isoformat() not in closed:
            out.append(d)
        d += timedelta(days=1)
    return out


def run(conn, start, markets=('ks', 'kq'), end=None, fetch=None, sleep=time.sleep, max_calls=None, now=None, log=print):
    """Returns a report dict. `fetch(config, day)` -> {'date','close'} | None (defaults to providers.krx_day)."""
    if fetch is None:
        from providers import MARKETS as CFG, krx_day as fetch  # noqa: N811  (v0.2 adapter, key via stdin)
    else:
        from providers import MARKETS as CFG
    today = kst_today(now)
    end = min(end or today - timedelta(days=1), today - timedelta(days=1))
    used = calls_today(conn, today)
    budget = max(0, min(max_calls if max_calls is not None else DAILY_BUDGET, DAILY_BUDGET - used))
    report = {'start': start.isoformat(), 'end': end.isoformat(), 'calls_before': used, 'budget': budget,
              'calls': 0, 'markets': {}, 'stopped': None}
    for market in markets:
        todo = pending_dates(conn, market, start, end, today)
        rep = report['markets'][market] = {'pending': len(todo), 'stored': 0, 'no_data': 0}
        for d in todo:
            if report['calls'] >= budget:
                report['stopped'] = 'daily budget reached (%d calls today, budget %d) · run again tomorrow' % (
                    used + report['calls'], DAILY_BUDGET)
                break
            if report['calls']:
                sleep(INTERVAL_S)
            try:
                row, made = _fetch_with_retry(fetch, CFG[market], d, sleep)
            except Exception as exc:   # auth / format, or still failing after retries: stop, keep progress
                for _ in range(getattr(exc, 'calls_made', 1)):
                    report['calls'] += 1
                    _count_call(conn, today)
                report['stopped'] = '%s %s: %s' % (CODES[market], d.isoformat(), exc)
                break
            report['retried'] = report.get('retried', 0) + made - 1
            for _ in range(made):
                report['calls'] += 1
                _count_call(conn, today)
            status = 'trading' if row else 'no_data'
            if row:
                store.upsert_prices(conn, market, [(row['date'], row['close'])], SOURCE_ID)
                rep['stored'] += 1
            else:
                rep['no_data'] += 1
            conn.execute('INSERT INTO krx_calendar (market, date, status, checked_at) VALUES (?,?,?,?) '
                         'ON CONFLICT(market, date) DO UPDATE SET status=excluded.status, checked_at=excluded.checked_at',
                         (market, d.isoformat(), status, store.now_utc()))
            if report['calls'] % 50 == 0:
                conn.commit()
                log('%s: %d/%d (stored %d, no data %d)' % (CODES[market], rep['stored'] + rep['no_data'], len(todo),
                                                          rep['stored'], rep['no_data']))
        conn.commit()
        rep['remaining'] = len(pending_dates(conn, market, start, end, today))
        if report['stopped']:
            break
    return report


def coverage(conn, market):
    row = conn.execute('SELECT COUNT(*), MIN(date), MAX(date) FROM prices WHERE market=? AND source_id=?',
                       (market, SOURCE_ID)).fetchone()
    return {'rows': row[0], 'first': row[1], 'last': row[2]}


def default_start(years=10, now=None):
    t = kst_today(now)
    return date(t.year - years, 1, 2)
