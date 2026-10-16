"""Read-only Toss Securities Open API client (market data only).

The Toss token carries no scopes: the same token could place orders. This module therefore
- calls only an allowlist of market-data paths (orders, accounts, holdings are refused before any request),
- never sends the X-Tossinvest-Account header,
- passes client secret and token to curl through stdin config, never argv, and keeps the token in memory.

Terms (FAQ "데이터 이용 정책", read 2026-10-16): "API를 통해 제공받는 정보는 투자자 본인의 매매 목적에 한하여
이용하여야 합니다. 상업적 이용이 제외될 뿐만 아니라 비상업적인 용도라 하더라도 제 3자에게 배포하는 행위는 엄격히 금지됩니다."
Calls must come from an IP registered in Toss WTS (403 ip-not-allowed otherwise).
"""
import json
import os
import re
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode

from . import store, timing

BASE = 'https://openapi.tossinvest.com'
ALLOWED = re.compile(r'^/(oauth2/token|api/v1/(market-indicators/prices|market-indicators/[A-Z0-9_]+/candles|candles|prices|'
                     r'exchange-rate|market-calendar/(KR|US)))$')
INDEX_SYMBOLS = {'ks': 'KOSPI', 'kq': 'KOSDAQ'}
PAGE = 200
INTERVAL_S = 0.25            # MARKET_INDICATOR_CHART allows 5 calls/s; stay well below


class TossError(RuntimeError):
    pass


def configured():
    return all(os.environ.get(k, '').strip() for k in ('TOSS_CLIENT_ID', 'TOSS_CLIENT_SECRET'))


def _curl(path, query=None, form=None, token=None):
    if not ALLOWED.match(path):
        raise PermissionError('Toss path not allowed (market data only): %s' % path)
    url = BASE + path + ('?' + urlencode(query, quote_via=quote) if query else '')
    config = 'url = "%s"\n' % url
    if token:
        if any(c in token for c in '\r\n"\\'):
            raise TossError('invalid token format')
        config += 'header = "Authorization: Bearer %s"\n' % token
    if form is not None:
        body = urlencode(form)
        if any(c in body for c in '\r\n"\\'):
            raise TossError('invalid credential format')
        config += 'header = "Content-Type: application/x-www-form-urlencoded"\ndata = "%s"\n' % body
    config += 'header = "Accept: application/json"\n'
    marker = '\n__TOSS_HTTP_STATUS__:'
    args = ['curl', '--silent', '--show-error', '--proto', '=https', '--connect-timeout', '10', '--max-time', '30',
            '--max-filesize', '5000000', '--config', '-', '--write-out', marker + '%{http_code}']
    try:
        res = subprocess.run(args, input=config, text=True, capture_output=True, timeout=35)
    except subprocess.TimeoutExpired:
        raise TossError('Toss 요청 시간 초과') from None
    body, _, status = res.stdout.rpartition(marker)
    status = status.strip()
    try:
        payload = json.loads(body) if body.strip() else {}
    except ValueError:
        payload = {}
    if res.returncode or not status.isdigit() or not 200 <= int(status) < 300:
        err = payload.get('error') if isinstance(payload, dict) else None
        code = (err or {}).get('code') if isinstance(err, dict) else (payload.get('error') if isinstance(payload, dict) else None)
        hint = {'401': '키 확인', '403': '허용 IP 등록 확인 (WTS 설정 > Open API > 허용 IP 관리)', '429': '호출 한도 초과'}
        raise TossError('Toss HTTP %s %s · %s' % (status, code or '', hint.get(status, '응답 확인')))
    return payload


class Client:
    def __init__(self, sleep=time.sleep):
        self._token, self._sleep = None, sleep

    def token(self):
        if self._token is None:
            if not configured():
                raise TossError('TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 없음 · ~/.config/mi/env 확인')
            r = _curl('/oauth2/token', form={'grant_type': 'client_credentials',
                                             'client_id': os.environ['TOSS_CLIENT_ID'].strip(),
                                             'client_secret': os.environ['TOSS_CLIENT_SECRET'].strip()})
            self._token = r.get('access_token') or ''
            if not self._token:
                raise TossError('Toss 토큰 응답에 access_token 없음')
        return self._token

    def get(self, path, **query):
        return _curl(path, {k: v for k, v in query.items() if v is not None}, token=self.token()).get('result')

    def index_prices(self, symbols=('KOSPI', 'KOSDAQ')):
        return self.get('/api/v1/market-indicators/prices', symbols=','.join(symbols))

    def daily(self, symbol, since=None, index=False):
        """Daily candles, oldest first: [{'date','close','open'}] (open None when absent). index=True for
        KOSPI/KOSDAQ, else a stock/ETF ticker. A completed US candle is the regular session: its open/close matched
        the 09:30 / 16:00 ET minute bars, not the Toss daytime/overnight session (SPY, QQQ 2026-10-14..15)."""
        path = '/api/v1/market-indicators/%s/candles' % symbol if index else '/api/v1/candles'
        out, before = {}, None
        while True:
            q = {'interval': '1d', 'count': PAGE, 'before': before}
            if not index:
                q.update(symbol=symbol, adjusted='true')
            page = self.get(path, **q) or {}
            for c in page.get('candles') or []:
                d = datetime.fromisoformat(c['timestamp']).date()
                close, open_ = float(c['closePrice']), float(c.get('openPrice') or 0)
                if close > 0:
                    out[d.isoformat()] = (close, open_ if open_ > 0 else None)
            before = page.get('nextBefore')
            if not before or (since and min(out, default='9999') < since.isoformat()):
                break
            self._sleep(INTERVAL_S)
        rows = [{'date': d, 'close': out[d][0], 'open': out[d][1]} for d in sorted(out)
                if not since or d >= since.isoformat()]
        return rows


ETF_PROXY = {'sp': 'SPY', 'nq': 'QQQ'}
INDEX_SOURCE, ETF_SOURCE = 'toss_openapi', 'toss_etf'
SETTLE = timedelta(minutes=15)       # a session's daily candle counts only 15 minutes after its close
HISTORY_START = date(2016, 1, 4)     # same span as the KRX archive
RECENT_DAYS = 14                     # re-read window: late corrections and missed runs


def complete(market, rows, now):
    """Drop the candle of a session that has not closed yet (an intraday price is not a close)."""
    return [r for r in rows if timing.close_utc(market, date.fromisoformat(r['date'])) + SETTLE <= now]


def sync(conn, client=None, now=None, log=print):
    """KOSPI/KOSDAQ recent closes (provisional until KRX publishes) and SPY/QQQ history into the archive.
    Returns a report; never raises for provider errors (the caller records them)."""
    now = now or datetime.now(timezone.utc)
    client = client or Client()
    rep = {'markets': {}, 'crosscheck': []}
    plan = [(m, s, True, INDEX_SOURCE) for m, s in INDEX_SYMBOLS.items()] + \
           [(m, s, False, ETF_SOURCE) for m, s in ETF_PROXY.items()]
    for market, symbol, index, source in plan:
        last = conn.execute('SELECT MAX(date) FROM prices WHERE market=? AND source_id=?', (market, source)).fetchone()[0]
        last_open = conn.execute('SELECT MAX(date) FROM price_opens WHERE market=? AND source_id=?',
                                 (market, source)).fetchone()[0]
        if index:   # closes: recent only (KRX is the record); opens: full history once, then recent
            since = now.date() - timedelta(days=RECENT_DAYS)
        else:
            since = (date.fromisoformat(last) - timedelta(days=RECENT_DAYS)) if last else HISTORY_START
        since_open = (date.fromisoformat(last_open) - timedelta(days=RECENT_DAYS)) if last_open else HISTORY_START
        try:
            rows = complete(market, client.daily(symbol, since=min(since, since_open), index=index), now)
        except (TossError, PermissionError) as exc:
            rep['markets'][market] = {'symbol': symbol, 'status': 'failed', 'error': str(exc)}
            continue
        ins, rev = store.upsert_prices(conn, market, [(r['date'], r['close']) for r in rows
                                                      if r['date'] >= since.isoformat()], source)
        o_ins, o_rev = store.upsert_opens(conn, market, [(r['date'], r['open']) for r in rows if r.get('open')], source)
        rows = [r for r in rows if r['date'] >= since.isoformat()]
        rep['markets'][market] = {'symbol': symbol, 'status': 'ok', 'source_id': source, 'inserted': ins,
                                  'revised': rev, 'opens_inserted': o_ins, 'opens_revised': o_rev,
                                  'last': rows[-1]['date'] if rows else None}
        if index:   # the same KRX index from two feeds: record any disagreement, never overwrite either
            krx = {r['date']: r['close'] for r in store.load_prices(conn, market, 'krx_openapi')}
            for r in rows:
                k = krx.get(r['date'])
                if k is not None and abs(k - r['close']) > 0.005:
                    rep['crosscheck'].append({'market': market, 'date': r['date'], 'krx': k, 'toss': r['close']})
                    conn.execute('INSERT INTO observation_revisions (kind, key, date, source_id, old_value, new_value, '
                                 'revised_at) VALUES (?,?,?,?,?,?,?)', ('crosscheck', market, r['date'], source, k,
                                                                         r['close'], store.now_utc()))
        log('%s (%s): +%d rev %d · opens +%d rev %d · last %s' % (symbol, source, ins, rev, o_ins, o_rev,
                                                                  rep['markets'][market]['last']))
    conn.commit()
    return rep
