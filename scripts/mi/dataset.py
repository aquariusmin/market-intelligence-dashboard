"""v0.5 point-in-time dataset: features X_t (known at the decision time of day t) and labels y_t.

Leakage controls
- Own-market features use closes <= t (causal rolling windows).
- Other markets / macro series enter via an as-of join on `available_at <= decision_time(t)`:
  e.g. a KOSPI forecast made at 16:00 KST on day t can see the US close of t-1 (05:00/06:00 KST on t)
  but never the US close of t (not yet happened).
- Labels need P_{t+h}; `outcome_pos = t + h` is kept so validation can purge overlapping windows.
- The neutral band uses volatility known at t and a constant fixed a priori (k = 0.43, ~1/3 of a
  Gaussian's mass) — it is never tuned on training / validation results.
"""
import hashlib
import json
import math

import numpy as np
import pandas as pd

from . import indicators, licenses, timing
from .macro import SERIES as MACRO

CLASSES = ['bearish', 'neutral', 'bullish']
NEUTRAL_K = 0.43
LABEL_VOL_WINDOW = 60
HORIZONS = (1, 5, 20)

OWN_FEATURES = ['ret1', 'ret5', 'ret20', 'ret60', 'dist_sma20', 'dist_sma50', 'dist_sma200', 'rsi14',
                'macd_hist_pct', 'vol20', 'vol60', 'vol_ratio', 'drawdown252']


def own_features(df):
    out = pd.DataFrame(index=df.index)
    lr = np.log(df['close'])
    for n in (1, 5, 20, 60):
        out['ret%d' % n] = (lr - lr.shift(n)) * 100
    for n in (20, 50, 200):
        out['dist_sma%d' % n] = df['dist_sma%d' % n]
    out['rsi14'] = df['rsi14']
    out['macd_hist_pct'] = df['macd_histogram'] / df['close'] * 100
    out['vol20'], out['vol60'] = df['vol20'], df['vol60']
    out['vol_ratio'] = df['vol20'] / df['vol60']
    out['drawdown252'] = df['drawdown252']
    return out


def macro_features(macro_rows, market, index):
    """-> (DataFrame, {feature: source_id})."""
    decisions = timing.decision_times(market, [d.date() for d in index])
    out, src = pd.DataFrame(index=index), {}
    derived = {
        'UST10Y': lambda s: {'ust10y': s, 'ust10y_chg20': s - s.shift(20)},
        'UST2Y': lambda s: {'ust2y_chg20': s - s.shift(20)},
        'UST3M': lambda s: {'ust3m_chg20': s - s.shift(20)},
        'USDKRW': lambda s: {'usdkrw_ret5': (s / s.shift(5) - 1) * 100, 'usdkrw_ret20': (s / s.shift(20) - 1) * 100},
        'USD_BROAD': lambda s: {'usd_broad_ret20': (s / s.shift(20) - 1) * 100},
        'VIX': lambda s: {'vix': s, 'vix_chg5': s - s.shift(5)},
        'HY_OAS': lambda s: {'hy_oas': s, 'hy_oas_chg20': s - s.shift(20)},
    }
    for sid, make in derived.items():
        rows = macro_rows.get(sid) or []
        if not rows:
            continue
        obs = [pd.Timestamp(d).date() for d, _ in rows]
        s = pd.Series([v for _, v in rows], index=pd.DatetimeIndex(pd.to_datetime([d for d, _ in rows])))
        avail = timing.macro_available_at(MACRO[sid]['lag'], obs)
        for name, series in make(s).items():
            out[name] = timing.asof_join(decisions, obs, avail, series.to_numpy(), name)
            src[name] = MACRO[sid]['source_id']
    if 'ust10y' in out and 'UST2Y' in macro_rows and macro_rows['UST2Y']:
        rows2 = macro_rows['UST2Y']
        obs = [pd.Timestamp(d).date() for d, _ in rows2]
        avail = timing.macro_available_at(MACRO['UST2Y']['lag'], obs)
        two = timing.asof_join(decisions, obs, avail, np.array([v for _, v in rows2]), 'ust2y')
        out['curve_2s10s'] = out['ust10y'] - two
        src['curve_2s10s'] = 'fed_h15'
    return out, src


def cross_features(market, index, own_close, others, manifest=None):
    """others: {market2: (dates, closes, source_id)}. As-of join on the other exchange's close time."""
    decisions = timing.decision_times(market, [d.date() for d in index])
    own_ret = np.log(own_close).diff()
    out, src = pd.DataFrame(index=index), {}
    for m2, (dates, closes, sid) in others.items():
        if m2 == market or len(dates) < 2:
            continue
        d2 = [pd.Timestamp(d).date() for d in dates]
        c2 = pd.Series(np.asarray(closes, float), index=pd.DatetimeIndex(pd.to_datetime(dates)))
        lr = np.log(c2)
        avail = timing.price_available_at(m2, d2, sid, manifest)
        feats = {'%s_ret1' % m2: (lr - lr.shift(1)) * 100, '%s_ret5' % m2: (lr - lr.shift(5)) * 100,
                 '%s_mom20' % m2: (c2 / c2.shift(20) - 1) * 100}
        for name, s in feats.items():
            out[name] = timing.asof_join(decisions, d2, avail, s.to_numpy(), name)
            src[name] = sid
        aligned = pd.Series(timing.asof_join(decisions, d2, avail, c2.to_numpy(), 'c'), index=index)
        out['%s_corr60' % m2] = own_ret.rolling(60, min_periods=60).corr(np.log(aligned).diff())
        src['%s_corr60' % m2] = sid
        own_mom = (own_close / own_close.shift(20) - 1) * 100
        out['rel_strength_%s' % m2] = own_mom - out['%s_mom20' % m2]
        src['rel_strength_%s' % m2] = sid
    return out, src


def labels(close, horizon, k=NEUTRAL_K, vol_window=LABEL_VOL_WINDOW):
    """y in {0: bearish, 1: neutral, 2: bullish} or NaN; threshold known at t; outcome at t+h."""
    fwd = close.shift(-horizon) / close - 1
    sigma = np.log(close).diff().rolling(vol_window, min_periods=vol_window).std(ddof=1)
    thr = k * sigma * math.sqrt(horizon)
    y = pd.Series(np.where(fwd > thr, 2, np.where(fwd < -thr, 0, 1)), index=close.index, dtype=float)
    y[fwd.isna() | thr.isna()] = np.nan
    return y, fwd, thr


def build(market, series_by_market, macro_rows, horizon, manifest=None):
    """series_by_market: {m: {'rows': [...], 'source_id': str}}. Returns dict or raises Gate error."""
    target = series_by_market[market]
    ok, reason = licenses.ml_allowed(target['source_id'], manifest)
    if not ok:
        raise GateError(reason)
    rows = target['rows']
    df = indicators.frame([r['date'] for r in rows], [r['close'] for r in rows])
    X = own_features(df)
    sources = {f: target['source_id'] for f in X.columns}
    dropped = []
    mx, msrc = macro_features(macro_rows, market, df.index)
    for col, sid in msrc.items():
        allowed, why = licenses.ml_allowed(sid, manifest)
        if allowed:
            X[col], sources[col] = mx[col], sid
        else:
            dropped.append({'feature': col, 'reason': why})
    others = {}
    for m2, s2 in series_by_market.items():
        if m2 == market or not s2.get('rows'):
            continue
        allowed, why = licenses.ml_allowed(s2['source_id'], manifest)
        if allowed:
            others[m2] = ([r['date'] for r in s2['rows']], [r['close'] for r in s2['rows']], s2['source_id'])
        else:
            dropped.append({'feature': '%s_*' % m2, 'reason': why})
    cx, csrc = cross_features(market, df.index, df['close'], others, manifest)
    for col in cx.columns:
        X[col], sources[col] = cx[col], csrc[col]
    y, fwd, thr = labels(df['close'], horizon)
    return {'market': market, 'horizon': horizon, 'X': X, 'y': y, 'forward_return': fwd, 'threshold': thr,
            'close': df['close'], 'outcome_pos': np.arange(len(df)) + horizon,
            'feature_sources': sources, 'dropped_features': dropped,
            'feature_hash': hashlib.sha1(json.dumps(sorted(X.columns)).encode()).hexdigest()[:8]}


class GateError(RuntimeError):
    """Training refused by the licence gate."""
