"""v0.4 rule-based market regime & risk. Arithmetic rules fixed a priori (not fitted to data).

Each historical label uses only data available at that day's decision time, so the regime
history is a point-in-time (filtered) record, never a smoothed / hindsight one.
A regime is a description of the current state, not a forecast of future returns.
"""
import math

import numpy as np
import pandas as pd

from . import indicators, timing

TREND_RULES = [
    ('close_gt_sma50', '종가 > 50일선', lambda r: r.close - r.sma50),
    ('close_gt_sma200', '종가 > 200일선', lambda r: r.close - r.sma200),
    ('sma50_gt_sma200', '50일선 > 200일선', lambda r: r.sma50 - r.sma200),
    ('mom20_pos', '20일 모멘텀 > 0', lambda r: r.mom20),
    ('mom60_pos', '60일 모멘텀 > 0', lambda r: r.mom60),
]
# value shown next to each rule (percent): distance or momentum, same sign as the rule test
RULE_VALUE = {'close_gt_sma50': lambda r: r.dist_sma50, 'close_gt_sma200': lambda r: r.dist_sma200,
              'sma50_gt_sma200': lambda r: (r.sma50 / r.sma200 - 1) * 100, 'mom20_pos': lambda r: r.mom20,
              'mom60_pos': lambda r: r.mom60}
TREND_BULL, TREND_BEAR = 3, -3        # score range -5..+5
VOL_HIGH_PCT = 0.70                    # 20d vol in top 30% of its trailing 252-day range
VOL_HIGH_ABS = 20.0                    # fallback (annualised %) when percentile history < 126 obs
CONFIRM_DAYS = 3                       # a new raw label must persist 3 sessions before the regime switches
RISK_BANDS = [(25, 'Low', '낮음'), (50, 'Moderate', '보통'), (75, 'Elevated', '높음'), (101, 'High', '매우 높음')]
REGIMES = ['Bullish / Low Volatility', 'Bullish / High Volatility', 'Bearish / Low Volatility',
           'Bearish / High Volatility', 'Sideways / Neutral']


def _sign(x):
    return 0 if x is None or not math.isfinite(x) or x == 0 else (1 if x > 0 else -1)


def classify_row(r):
    if not all(math.isfinite(getattr(r, k)) for k in ('sma200', 'mom60', 'vol20')):
        return None, None, None, None
    score = sum(_sign(f(r)) for _, _, f in TREND_RULES)
    high = r.vol20_pct >= VOL_HIGH_PCT if math.isfinite(r.vol20_pct) else r.vol20 >= VOL_HIGH_ABS
    vol = 'High Volatility' if high else 'Low Volatility'
    if score >= TREND_BULL:
        label = 'Bullish / ' + vol
    elif score <= TREND_BEAR:
        label = 'Bearish / ' + vol
    else:
        label = 'Sideways / Neutral'
    return label, score, vol, bool(high)


def confirm(raw, n=CONFIRM_DAYS):
    """Causal hysteresis: switch only after n consecutive identical raw labels (no look-ahead)."""
    out, cur, run_label, run = [], None, None, 0
    for lab in raw:
        if lab is None:
            out.append(None)
            continue
        run = run + 1 if lab == run_label else 1
        run_label = lab
        if cur is None or (lab != cur and run >= n):
            cur = lab
        out.append(cur)
    return out


def _clip01(x):
    return None if x is None or not math.isfinite(x) else min(max(x, 0.0), 1.0)


def macro_frame(macro_rows, market, dates):
    """Macro values as known at each decision time of `market` (point-in-time asof join)."""
    from .macro import SERIES
    decisions = timing.decision_times(market, [d.date() for d in dates])
    out = pd.DataFrame(index=dates)
    for sid, rows in macro_rows.items():
        if not rows:
            continue
        obs = [pd.Timestamp(d).date() for d, _ in rows]
        s = pd.Series([v for _, v in rows], index=pd.DatetimeIndex([pd.Timestamp(d) for d, _ in rows]))
        avail = timing.macro_available_at(SERIES[sid]['lag'], obs)
        derived = {sid: s,
                   sid + '_chg20': s - s.shift(20),
                   sid + '_ret20': (s / s.shift(20) - 1) * 100,
                   sid + '_pct': indicators.trailing_percentile(s, 252, 126)}
        for name, series in derived.items():
            out[name] = timing.asof_join(decisions, obs, avail, series.to_numpy(), name)
        # observation date of the value known at each decision (display only, e.g. "(기준 10-13)")
        out[sid + '_obs'] = timing.asof_join(decisions, obs, avail, np.array([d.isoformat() for d in obs], dtype=object),
                                             sid + '_obs')
    return out


def risk_components(row, market, m):
    """Each component -> 0..1 stress score (None if unavailable). Fixed mappings, documented in README."""
    g = lambda k: m.get(k) if m is not None and k in m and m[k] is not None and math.isfinite(m[k]) else None
    comps = {
        'realized_vol': _clip01(row.vol20_pct) if math.isfinite(row.vol20_pct) else _clip01(row.vol20 / 40),
        'drawdown': _clip01(-row.drawdown252 / 20),
        'vix': _clip01((g('VIX') - 12) / 23) if g('VIX') is not None else None,          # 12 -> 0, 35 -> 1
        'credit': _clip01(g('HY_OAS_pct')) if g('HY_OAS_pct') is not None else None,
        'rates': _clip01(abs(g('UST10Y_chg20')) / 0.5) if g('UST10Y_chg20') is not None else None,  # 50bp/20d -> 1
    }
    if market in ('ks', 'kq'):
        comps['krw'] = _clip01(g('USDKRW_ret20') / 3) if g('USDKRW_ret20') is not None else None  # +3% -> 1
    return comps


def risk_level(comps):
    vals = [v for v in comps.values() if v is not None]
    if not vals:
        return None, None, None
    score = round(100 * sum(vals) / len(vals), 1)
    for limit, en, ko in RISK_BANDS:
        if score < limit:
            return score, en, ko


def factors(row, m, market):
    """Human-readable bullish / bearish factors from current values (no forecasting claims)."""
    bull, bear = [], []
    f = lambda v, d=1: ('%+.' + str(d) + 'f') % v
    if math.isfinite(row.sma200):
        (bull if row.close > row.sma200 else bear).append(
            '종가가 200일 이동평균 %s (%s%%)' % ('위' if row.close > row.sma200 else '아래', f(row.dist_sma200)))
    if math.isfinite(row.mom20):
        (bull if row.mom20 > 0 else bear).append('20거래일 모멘텀 %s%%' % f(row.mom20, 2))
    if math.isfinite(row.vol20_pct):
        if row.vol20_pct >= VOL_HIGH_PCT:
            top = round((1 - row.vol20_pct) * 100)
            bear.append('20일 실현변동성 %.1f%% · %s' % (row.vol20, '최근 1년 최고 수준' if top < 1 else
                                                     '최근 1년 중 상위 %d%%' % top))
        elif row.vol20_pct <= 0.3:
            bull.append('20일 실현변동성 %.1f%% · 최근 1년 대비 낮은 수준' % row.vol20)
    if math.isfinite(row.drawdown252) and row.drawdown252 <= -10:
        bear.append('52주 고점 대비 %.1f%%' % row.drawdown252)
    if math.isfinite(row.rsi14):
        if row.rsi14 >= 70:
            bear.append('RSI %.1f · 단기 과열 구간' % row.rsi14)
        elif row.rsi14 <= 30:
            bull.append('RSI %.1f · 단기 과매도 구간' % row.rsi14)
    if m is not None:
        v = m.get('VIX')
        if v is not None and math.isfinite(v):
            (bear if v >= 25 else bull if v < 16 else []).append('VIX %.1f' % v) if (v >= 25 or v < 16) else None
        c = m.get('HY_OAS_chg20')
        if c is not None and math.isfinite(c) and abs(c) >= 0.25:
            (bear if c > 0 else bull).append('하이일드 스프레드 20일간 %+.0fbp' % (c * 100))
        r = m.get('UST10Y_chg20')
        if r is not None and math.isfinite(r) and abs(r) >= 0.25:
            # Same definition as the 'rates' risk component (abs change = rate-volatility stress): a large move
            # in either direction is listed as a risk, never as a bullish factor.
            obs = m.get('UST10Y_obs')
            ref = ' (기준 %s)' % obs[5:] if isinstance(obs, str) else ''
            bear.append('미국 10년물 금리 20일간 %+.0fbp · 금리 변동성 확대%s' % (r * 100, ref))
        k = m.get('USDKRW_ret20')
        if market in ('ks', 'kq') and k is not None and math.isfinite(k) and abs(k) >= 1.5:
            (bear if k > 0 else bull).append('원화 %s (USD/KRW 20일 %+.1f%%)' % ('약세' if k > 0 else '강세', k))
    return bull, bear


def _num(v, d=4):
    return None if v is None or not math.isfinite(v) else round(float(v), d)


def _state(ts, row, mrow, market):
    """Comparable point-in-time state of one session (used for the current and the previous session)."""
    comps = risk_components(row, market, mrow)
    score, band_en, band_ko = risk_level(comps)
    return {'as_of': ts.date().isoformat(), 'regime': row.regime, 'raw_regime': row.raw_regime,
            'trend_score': None if row.trend_score is None or not math.isfinite(row.trend_score) else int(row.trend_score),
            'vol20_pct': _num(row.vol20_pct, 3),
            'risk': {'score': score, 'level': band_en, 'level_ko': band_ko,
                     'components': {k: _num(v, 3) for k, v in comps.items()}}}


def _pending_days(raw, confirmed):
    """Consecutive sessions the latest raw label has persisted while the confirmed regime differs."""
    if not raw or raw[-1] is None or raw[-1] == confirmed:
        return 0
    n = 0
    for lab in reversed(raw):
        if lab != raw[-1]:
            break
        n += 1
    return n


def analyze(market, price_rows, macro_rows, history_len=260):
    """price_rows: [{'date','close'}] chronological. Returns JSON-ready dict."""
    if len(price_rows) < 2:
        return {'market': market, 'status': 'unavailable', 'reason': '가격 이력 없음'}
    df = indicators.frame([r['date'] for r in price_rows], [r['close'] for r in price_rows])
    mf = macro_frame(macro_rows, market, df.index)
    labels, scores, risks = [], [], []
    for (ts, row), (_, mrow) in zip(df.iterrows(), mf.iterrows()):
        label, score, vol, high = classify_row(row)
        labels.append(label)
        scores.append(score)
        risks.append(risk_level(risk_components(row, market, mrow.to_dict()))[0] if label else None)
    df['raw_regime'], df['trend_score'], df['risk_score'] = labels, scores, risks
    df['regime'] = confirm(labels)
    last, mlast = df.iloc[-1], mf.iloc[-1].to_dict()
    if last.regime is None:
        return {'market': market, 'status': 'insufficient_history', 'observations': len(df),
                'reason': '200일 이동평균·60일 모멘텀 계산에 필요한 이력 부족 (%d관측)' % len(df)}
    comps = risk_components(last, market, mlast)
    score, band_en, band_ko = risk_level(comps)
    valid = df.dropna(subset=['regime'])
    transitions, prev = [], None
    for ts, lab in valid['regime'].items():
        if prev is not None and lab != prev:
            transitions.append({'date': ts.date().isoformat(), 'from': prev, 'to': lab})
        prev = lab
    since = transitions[-1]['date'] if transitions else valid.index[0].date().isoformat()
    bull, bear = factors(last, mlast, market)
    num = _num
    tail = valid.iloc[-history_len:]
    prev = None
    if len(df) >= 2 and df.iloc[-2].regime is not None:
        prev = _state(df.index[-2], df.iloc[-2], mf.iloc[-2].to_dict(), market)
    _, _, cur_vol, _ = classify_row(last)
    return {
        'market': market, 'status': 'ok', 'as_of': df.index[-1].date().isoformat(), 'observations': len(df),
        'regime': last.regime, 'raw_regime': last.raw_regime, 'confirm_days': CONFIRM_DAYS,
        'trend_score': int(last.trend_score), 'volatility_state': cur_vol,
        'regime_since': since, 'regime_days': int((valid.index > pd.Timestamp(since)).sum()) + 1,
        'trend_rules': [{'id': k, 'label': lab, 'pass': _sign(fn(last)) > 0, 'value': num(RULE_VALUE[k](last), 2)}
                        for k, lab, fn in TREND_RULES],
        'pending_days': _pending_days(labels, last.regime),
        'vol_basis': {'percentile': num(last.vol20_pct, 3), 'threshold': VOL_HIGH_PCT,
                      'fallback_abs': not math.isfinite(last.vol20_pct), 'abs_threshold': VOL_HIGH_ABS},
        'previous': prev,
        'short_term': _short(last), 'medium_term': _medium(last),
        'metrics': {k: num(last[k]) for k in ('close', 'sma20', 'sma50', 'sma200', 'dist_sma20', 'dist_sma50',
                                              'dist_sma200', 'mom5', 'mom20', 'mom60', 'vol20', 'vol60',
                                              'vol20_pct', 'drawdown252', 'rsi14', 'macd_histogram')},
        'risk': {'score': score, 'level': band_en, 'level_ko': band_ko,
                 'components': {k: num(v, 3) for k, v in comps.items()},
                 # score averages only the available components: expose coverage, e.g. "3/5"
                 'components_available': '%d/%d' % (sum(v is not None for v in comps.values()), len(comps)),
                 'components_missing': [k for k, v in comps.items() if v is None]},
        'bullish_factors': bull, 'bearish_factors': bear,
        'history': [{'date': ts.date().isoformat(), 'regime': r.regime, 'raw': r.raw_regime,
                     'trend_score': int(r.trend_score),
                     'risk': num(r.risk_score, 1), 'close': num(r.close, 2), 'vol20': num(r.vol20, 2)}
                    for ts, r in tail.iterrows()],
        'vol_history': [{'date': ts.date().isoformat(), 'vol20': num(r.vol20, 2), 'vol60': num(r.vol60, 2)}
                        for ts, r in df.iloc[-history_len:].iterrows() if math.isfinite(r.vol20)],
        'transitions': transitions[-12:],
        'counts': {k: int((tail['regime'] == k).sum()) for k in REGIMES},
    }


def _short(r):
    s = _sign(r.dist_sma20) + _sign(r.mom5)
    return '상승' if s == 2 else '하락' if s == -2 else '혼조'


def _medium(r):
    if not (math.isfinite(r.sma200) and math.isfinite(r.mom60)):
        return '이용 불가'
    s = _sign(r.sma50 - r.sma200) + _sign(r.mom60)
    return '상승' if s == 2 else '하락' if s == -2 else '혼조'


def global_risk(macro_rows):
    """Latest macro risk dashboard values (as of collection, display only)."""
    from .macro import SERIES
    out = {}
    for sid, rows in macro_rows.items():
        if not rows:
            continue
        s = pd.Series([v for _, v in rows], index=pd.DatetimeIndex([pd.Timestamp(d) for d, _ in rows]))
        last = float(s.iloc[-1])
        chg = lambda n: float(s.iloc[-1] - s.iloc[-1 - n]) if len(s) > n else None
        pct = indicators.trailing_percentile(s, 252, 126).iloc[-1]
        out[sid] = {'name': SERIES[sid]['name'], 'unit': SERIES[sid]['unit'], 'source_id': SERIES[sid]['source_id'],
                    'as_of': s.index[-1].date().isoformat(), 'value': round(last, 4),
                    'chg5': None if chg(5) is None else round(chg(5), 4),
                    'chg20': None if chg(20) is None else round(chg(20), 4),
                    'pct1y': None if not np.isfinite(pct) else round(float(pct), 3),
                    'history': [{'date': ts.date().isoformat(), 'value': round(float(v), 4)}
                                for ts, v in s.iloc[-520:].items()]}
    if 'UST10Y' in out and 'UST2Y' in out and out['UST10Y']['as_of'] == out['UST2Y']['as_of']:
        out['CURVE_2S10S'] = {'name': '10년-2년 금리차', 'unit': '%p', 'source_id': 'fed_h15',
                              'as_of': out['UST10Y']['as_of'],
                              'value': round(out['UST10Y']['value'] - out['UST2Y']['value'], 4)}
    return out
