#!/usr/bin/env python3
"""Manual EOD collection. Python stdlib + system curl; atomic last-good JSON."""
import argparse
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from providers import MARKETS, PROVIDERS, krx_day


def indicators(rows):
    values = [r['close'] for r in rows]
    def sma(n):
        return sum(values[-n:]) / n if len(values) >= n else None
    rsi = None
    if len(values) >= 15:
        changes = [b-a for a, b in zip(values, values[1:])]
        gain = sum(max(d, 0) for d in changes[:14]) / 14
        loss = sum(max(-d, 0) for d in changes[:14]) / 14
        for delta in changes[14:]:
            gain = (gain*13 + max(delta, 0)) / 14
            loss = (loss*13 + max(-delta, 0)) / 14
        rsi = 50.0 if gain == loss == 0 else (100.0 if loss == 0 else 100 - 100/(1+gain/loss))
    def ema_series(n):
        out = [None] * len(values)
        if len(values) >= n:
            out[n-1] = sum(values[:n]) / n
            for i in range(n, len(values)):
                out[i] = values[i]*2/(n+1) + out[i-1]*(1-2/(n+1))
        return out
    e12, e26 = ema_series(12), ema_series(26)
    macds = [a-b for a, b in zip(e12, e26) if a is not None and b is not None]
    signal = None
    if len(macds) >= 9:
        signal = sum(macds[:9]) / 9
        for m in macds[9:]:
            signal = m*.2 + signal*.8
    logs = [math.log(b/a) for a, b in zip(values, values[1:])]
    result = {'sma20': sma(20), 'sma50': sma(50), 'sma200': sma(200), 'rsi14': rsi,
            'macd': macds[-1] if macds else None, 'macd_signal': signal,
            'macd_histogram': macds[-1]-signal if signal is not None else None,
            'return20': (values[-1]/values[-21]-1)*100 if len(values) >= 21 else None,
            'realized_volatility20': statistics.stdev(logs[-20:])*math.sqrt(252)*100 if len(logs) >= 20 else None}

    for n in (5, 60):
        result['return'+str(n)] = (values[-1]/values[-n-1]-1)*100 if len(values)>n else None
    result['realized_volatility60'] = statistics.stdev(logs[-60:])*math.sqrt(252)*100 if len(logs)>=60 else None
    cutoff = date.fromisoformat(rows[-1]['date']) - timedelta(weeks=52)
    high = max(r['close'] for r in rows if date.fromisoformat(r['date'])>=cutoff) if date.fromisoformat(rows[0]['date'])<=cutoff else None
    result['high52w'] = high
    result['drawdown52w'] = (values[-1]/high-1)*100 if high else None
    for n in (20, 50, 200):
        result['distance_sma'+str(n)] = (values[-1]/result['sma'+str(n)]-1)*100 if result['sma'+str(n)] else None
    return result


def market_regime(value, metrics):
    required = ['sma20', 'sma50', 'sma200', 'return20', 'realized_volatility20']
    if any(metrics.get(k) is None for k in required):
        return {'code':'unavailable', 'label':'이력 부족', 'rules_version':'1'}
    a,b,c = (metrics['sma'+str(n)] for n in (20,50,200))
    code = 'uptrend' if value>=a>=b>=c and metrics['return20']>0 else (
        'downtrend' if value<a<b<c and metrics['return20']<0 else 'mixed')
    vol = metrics['realized_volatility20']
    return {'code':code, 'label':{'uptrend':'상승 추세','downtrend':'하락 추세','mixed':'혼조 / 횡보'}[code],
            'volatility':'높음' if vol>=25 else ('낮음' if vol<20 else '보통'), 'rules_version':'1'}


def validate_rows(rows, today):
    if not isinstance(rows, list) or len(rows) < 2:
        raise ValueError('유효 일별 종가가 2개 미만')
    result, last = [], ''
    for row in rows:
        d = date.fromisoformat(row['date'])
        c = row['close']
        if row['date'] <= last or d > today or isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c) or c <= 0:
            raise ValueError('날짜 순서/중복/미래일 또는 종가 오류')
        result.append({'date': d.isoformat(), 'close': c})
        last = row['date']
    return result


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, suffix='.tmp', delete=False) as f:
            temp = f.name
            json.dump(value, f, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)


def collect(output, providers=None, today=None, selected=None):
    providers = PROVIDERS if providers is None else providers
    # Asia/Seoul is the furthest-ahead market clock here, so a valid KST-dated close is never 'future'
    today = today or datetime.now(ZoneInfo('Asia/Seoul')).date()
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    old = {'schema_version': 1, 'markets': {}}
    if output.exists():
        # Corrupt snapshots are not silently overwritten: repair or move them manually.
        old = json.loads(output.read_text(encoding='utf-8'))
        if old.get('schema_version') != 1 or not isinstance(old.get('markets'), dict):
            raise ValueError('기존 JSON 스키마 오류: 원본을 보존하고 종료')
    result = {'schema_version': 1, 'last_attempt_at': now,
              'last_success_at': old.get('last_success_at'), 'markets': {}}
    failures = 0
    successes = 0
    for key, config in MARKETS.items():
        previous = old['markets'].get(key, {})
        if selected is not None and key not in selected:
            result['markets'][key] = previous or dict(config, status='unavailable',
                error='이번 실행에서 수집하지 않은 시장', history=[], indicators={},
                value=None, change_pct=None, as_of=None, updated_at=None, last_attempt_at=None)
            continue
        try:
            rows = validate_rows(providers[config['provider']](config, previous.get('history', []), today), today)
            if previous.get('as_of') and rows[-1]['date'] < previous['as_of']:
                raise ValueError('제공자 데이터가 이전 정상 데이터보다 과거임')
            if (previous.get('history') and len(rows) < min(200, len(previous['history']))):
                raise ValueError('비정상적으로 짧은 이력: 이전 데이터 유지')
            # Reject very old responses as a successful refresh; existing old snapshots stay visible.
            if (today - date.fromisoformat(rows[-1]['date'])).days > 10:
                raise ValueError('제공자 최근 관측값이 10일 이상 지연됨')
            metrics = indicators(rows)
            entry = dict(config, status='ok', error=None, last_attempt_at=now,
                         updated_at=now, as_of=rows[-1]['date'], history=rows,
                         value=rows[-1]['close'], change_pct=(rows[-1]['close']/rows[-2]['close']-1)*100,
                         indicators=metrics,
                         regime=market_regime(rows[-1]['close'], metrics),
                         usage={'scope':'local_reference_only','ml_training_allowed':False})
            successes += 1
            print('%s: OK · %s · %d observations' % (config['name'], entry['as_of'], len(rows)))
        except Exception as exc:
            failures += 1
            has_previous = bool(previous.get('history'))
            entry = dict(previous) if has_previous else dict(config, history=[], indicators={}, value=None, change_pct=None, as_of=None, updated_at=None)
            entry.update(status='stale' if has_previous else 'unavailable', error=str(exc), last_attempt_at=now)
            print('%s: %s · %s' % (config['name'], entry['status'], exc), file=sys.stderr)
        result['markets'][key] = entry
    if successes:
        result['last_success_at'] = now
    atomic_json(output, result)
    return failures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1]/'data'/'markets.json')
    parser.add_argument('--check-krx', type=date.fromisoformat, metavar='YYYY-MM-DD',
                        help='두 KRX API를 날짜별 1회씩 진단. JSON 파일을 변경하지 않음')
    parser.add_argument('--markets', nargs='+', choices=list(MARKETS),
                        help='수집할 시장만 선택: sp nq ks kq. 나머지는 기존 데이터 유지')
    parser.add_argument('--recalculate', action='store_true', help='네트워크 없이 기존 정상 이력의 v0.3 분석 갱신')
    args = parser.parse_args()
    if args.check_krx and args.markets and any(k not in ('ks', 'kq') for k in args.markets):
        parser.error('--check-krx에는 --markets ks kq만 지정할 수 있습니다')
    try:
        if args.recalculate:
            data = json.loads(args.output.read_text(encoding='utf-8'))
            if data.get('schema_version') != 1 or not isinstance(data.get('markets'), dict):
                raise ValueError('기존 JSON 스키마 오류')
            for market in data['markets'].values():
                if market.get('history'):
                    rows = validate_rows(market['history'], date.today())
                    market['indicators'] = indicators(rows)
                    market['regime'] = market_regime(market['value'], market['indicators'])
                    market['usage'] = {'scope':'local_reference_only', 'ml_training_allowed':False}
            data['analytics_computed_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
            atomic_json(args.output, data)
            print('v0.3 분석 갱신 완료 · 원본 관측일/수집시각 유지')
            sys.exit(0)
        if args.check_krx:
            failures = 0
            for key in (args.markets or ('ks', 'kq')):
                try:
                    row = krx_day(MARKETS[key], args.check_krx)
                    if row is None:
                        failures += 1
                        print('%s: HTTP 성공, 해당일 데이터 없음 (휴장/미게시 확인)' % MARKETS[key]['name'])
                    else:
                        if not math.isfinite(row['close']) or row['close'] <= 0:
                            raise ValueError('종가 오류')
                        print('%s: OK · %s · close=%s' % (MARKETS[key]['name'], row['date'], row['close']))
                except Exception as exc:
                    failures += 1
                    print('%s: %s' % (MARKETS[key]['name'], exc), file=sys.stderr)
            sys.exit(1 if failures else 0)
        sys.exit(1 if collect(args.output, selected=args.markets) else 0)
    except Exception as exc:
        print('Collection aborted; snapshot unchanged: %s' % exc, file=sys.stderr)
        sys.exit(2)
