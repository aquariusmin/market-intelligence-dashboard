"""Causal (past-only) indicator series. Value at row t uses closes[0..t] only.

Formulas match scripts/collect_data.py (v0.2) so the last row equals the snapshot indicators.
"""
import numpy as np
import pandas as pd

ANNUAL = 252


def sma(close, n):
    return close.rolling(n, min_periods=n).mean()


def ema_sma_seeded(values, n):
    """EMA seeded with the SMA of the first n values (v0.2 convention). NaNs before seed."""
    v = np.asarray(values, dtype=float)
    out = np.full(len(v), np.nan)
    finite = np.flatnonzero(~np.isnan(v))
    if len(finite) < n:
        return out
    start = finite[0]
    if start + n > len(v) or np.isnan(v[start:start + n]).any():
        return out
    alpha = 2 / (n + 1)
    out[start + n - 1] = v[start:start + n].mean()
    for i in range(start + n, len(v)):
        out[i] = v[i] * alpha + out[i - 1] * (1 - alpha)
    return out


def rsi_wilder(close, n=14):
    v = close.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    if len(v) < n + 1:
        return pd.Series(out, index=close.index)
    d = np.diff(v)
    gain = np.maximum(d[:n], 0).mean()
    loss = np.maximum(-d[:n], 0).mean()

    def value(g, l):
        return 50.0 if g == l == 0 else (100.0 if l == 0 else 100 - 100 / (1 + g / l))
    out[n] = value(gain, loss)
    for i in range(n, len(d)):
        gain = (gain * (n - 1) + max(d[i], 0)) / n
        loss = (loss * (n - 1) + max(-d[i], 0)) / n
        out[i + 1] = value(gain, loss)
    return pd.Series(out, index=close.index)


def macd(close):
    e12 = ema_sma_seeded(close, 12)
    e26 = ema_sma_seeded(close, 26)
    line = e12 - e26
    signal = np.full(len(line), np.nan)
    finite = np.flatnonzero(~np.isnan(line))
    if len(finite) >= 9:
        signal[finite[0]:] = ema_sma_seeded(line[finite[0]:], 9)
    return (pd.Series(line, index=close.index), pd.Series(signal, index=close.index),
            pd.Series(line - signal, index=close.index))


def log_returns(close):
    return np.log(close / close.shift(1))


def realized_vol(close, n):
    """Annualised sample sd (ddof=1) of the last n log returns, in percent."""
    return log_returns(close).rolling(n, min_periods=n).std(ddof=1) * np.sqrt(ANNUAL) * 100


def momentum(close, n):
    return (close / close.shift(n) - 1) * 100


def drawdown(close, window=252):
    """Percent below the trailing `window`-observation high (including today)."""
    peak = close.rolling(window, min_periods=1).max()
    return (close / peak - 1) * 100


def trailing_percentile(series, window=252, min_periods=126):
    """Rank of today's value within the trailing window (past and today only), 0..1."""
    def rank(a):
        return (a[:-1] < a[-1]).mean() if len(a) > 1 else np.nan
    return series.rolling(window, min_periods=min_periods).apply(rank, raw=True)


def frame(dates, closes):
    """Full causal indicator table for one market."""
    close = pd.Series(np.asarray(closes, dtype=float), index=pd.DatetimeIndex(pd.to_datetime(dates)), name='close')
    df = pd.DataFrame({'close': close})
    for n in (20, 50, 200):
        df['sma%d' % n] = sma(close, n)
        df['dist_sma%d' % n] = (close / df['sma%d' % n] - 1) * 100
    df['rsi14'] = rsi_wilder(close)
    df['macd'], df['macd_signal'], df['macd_histogram'] = macd(close)
    for n in (1, 5, 20, 60):
        df['mom%d' % n] = momentum(close, n)
    df['vol20'] = realized_vol(close, 20)
    df['vol60'] = realized_vol(close, 60)
    df['vol20_pct'] = trailing_percentile(df['vol20'])
    df['drawdown252'] = drawdown(close)
    return df
