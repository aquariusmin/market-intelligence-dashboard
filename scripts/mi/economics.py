"""Cost-aware evaluation of a model's holdout signals: would acting on them have beaten simply holding?

Rule, costs and verdict were fixed on 2026-10-16 before any result was computed:
- long/flat: hold the index for h sessions when the predicted class (argmax) is bullish, otherwise cash;
  no short selling (individual investors trade these indices through ETFs);
- non-overlapping periods: holdout signal days spaced h sessions apart, so returns do not overlap;
- entry at the signal day's close ("same_close": Toss same-day close + after-hours closing-price trade) or one
  session later ("next_close": a conservative stand-in for next-open entry; opening prices are not stored);
- round-trip cost per trade: KOSPI/KOSDAQ 0.10 %, S&P 500/NASDAQ 100 (ETF) 0.25 %;
- verdict "비용 차감 후 우위" only if the moving-block bootstrap 90 % lower bound of the mean per-period
  excess return over buy-and-hold is above zero.
Index or ETF price returns only (no dividends, no taxes, no FX for US ETFs bought in KRW).
"""
import math

import numpy as np

from .validation import block_bootstrap_diff

ROUND_TRIP_COST = {'ks': 0.0010, 'kq': 0.0010, 'sp': 0.0025, 'nq': 0.0025}
DELAYS = {'same_close': 0, 'next_close': 1}
BOOT_REPS = 1000
MIN_PERIODS = 20


def _max_drawdown(returns):
    equity = np.cumprod(1 + np.asarray(returns, float))
    peak = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    return float((equity / peak - 1).min()) if len(equity) else None


def periods(idx, horizon, n_rows, delay):
    """Non-overlapping signal positions within the holdout, each with a complete holding window."""
    idx = sorted(int(i) for i in idx)
    if not idx:
        return []
    start, have = idx[0], set(idx)
    return [i for i in range(start, idx[-1] + 1, horizon) if i in have and i + delay + horizon < n_rows]


def evaluate(close, idx, proba, horizon, market, delay=0, seed=0):
    close = np.asarray(close, float)
    pos = {int(i): k for k, i in enumerate(idx)}
    cost = ROUND_TRIP_COST[market]
    strat, hold, trades = [], [], []
    for i in periods(idx, horizon, len(close), delay):
        entry, exit_ = i + delay, i + delay + horizon
        r = close[exit_] / close[entry] - 1
        long_ = int(np.argmax(proba[pos[i]])) == 2
        strat.append(r - cost if long_ else 0.0)
        hold.append(r)
        if long_:
            trades.append(r - cost)
    n = len(strat)
    if n < MIN_PERIODS:
        return {'status': 'insufficient', 'periods': n, 'reason': '비중첩 기간 %d < %d' % (n, MIN_PERIODS)}
    block = max(2, int(math.ceil(n ** (1 / 3))))
    # block_bootstrap_diff returns mean(a - b) and its 90 % interval: a = strategy, b = buy-and-hold
    mean_excess, lo, hi = block_bootstrap_diff(np.asarray(strat), np.asarray(hold), block, BOOT_REPS, seed)
    comp = lambda x: float(np.prod(1 + np.asarray(x)) - 1)
    out = {
        'status': 'ok', 'delay_sessions': delay, 'round_trip_cost': cost, 'periods': n, 'trades': len(trades),
        'exposure': len(trades) / n, 'hit_rate': (sum(t > 0 for t in trades) / len(trades)) if trades else None,
        'mean_trade_net': float(np.mean(trades)) if trades else None,
        'strategy_total': comp(strat), 'buy_hold_total': comp(hold),
        'strategy_max_drawdown': _max_drawdown(strat), 'buy_hold_max_drawdown': _max_drawdown(hold),
        'mean_excess_vs_hold': mean_excess, 'excess_ci90': [lo, hi],
    }
    out['verdict'] = verdict(out)
    return out


def verdict(r):
    """Wording only; the single pass criterion (90 % lower bound of excess > 0) is fixed above."""
    lo, mean = (r.get('excess_ci90') or [None])[0], r.get('mean_excess_vs_hold')
    if lo is not None and lo > 0:
        return '비용 차감 후 우위'
    if mean is not None and mean > 0:
        return '우위 근거 부족 (90% 구간이 0 포함)'
    if r.get('strategy_total', 0) > 0:
        return '수익은 양(+)이나 단순 보유보다 낮음'
    return '우위 없음'


def report(close, idx, proba, horizon, market, seed=0):
    return {name: evaluate(close, idx, proba, horizon, market, delay, seed) for name, delay in DELAYS.items()}
