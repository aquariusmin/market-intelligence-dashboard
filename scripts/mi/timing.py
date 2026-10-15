"""Point-in-time availability rules.

Every observation gets an `available_at` UTC timestamp; every forecast has a
`decision_time` UTC timestamp. A feature may be used only if available_at <= decision_time.
zoneinfo handles US daylight saving time (16:00 ET = 05:00 KST in summer, 06:00 KST in winter).
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo('America/New_York')
KST = ZoneInfo('Asia/Seoul')

# Session close in exchange local time; forecasts are generated 30 minutes after the close.
SESSIONS = {
    'sp': (ET, time(16, 0)), 'nq': (ET, time(16, 0)),
    'ks': (KST, time(15, 30)), 'kq': (KST, time(15, 30)),
}
DECISION_DELAY = timedelta(minutes=30)
PRICE_PUBLICATION_DELAY = timedelta(minutes=15)

# Conservative publication lags for macro series (series date d -> first time we could know it).
# H.15 daily yields: posted on the following business day (DDP) -> d+1 17:00 ET.
# H.10 daily FX: released weekly, normally Monday 16:15 ET for the prior week -> Tuesday after week end.
# FRED-hosted Cboe/ICE series: posted the following morning -> d+1 18:00 ET (conservative).
MACRO_LAG = {
    'next_business_day_17et': lambda d: _local(_next_weekday(d), time(17, 0), ET),
    'weekly_h10': lambda d: _local(d + timedelta(days=(7 - d.weekday()) + 1), time(16, 15), ET),
    'next_day_18et': lambda d: _local(_next_weekday(d), time(18, 0), ET),
}


def _next_weekday(d):
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _local(d, t, tz):
    return datetime.combine(d, t, tz).astimezone(timezone.utc)


def close_utc(market, d):
    tz, t = SESSIONS[market]
    return _local(d, t, tz)


def price_available_at(market, dates):
    """dates: iterable of datetime.date -> pandas UTC timestamps."""
    return pd.to_datetime([close_utc(market, d) + PRICE_PUBLICATION_DELAY for d in dates], utc=True)


def decision_time(market, d):
    return close_utc(market, d) + DECISION_DELAY


def decision_times(market, dates):
    return pd.to_datetime([decision_time(market, d) for d in dates], utc=True)


def macro_available_at(lag_rule, dates):
    rule = MACRO_LAG[lag_rule]
    return pd.to_datetime([rule(d) for d in dates], utc=True)


def asof_join(decisions, observed, available, values, name):
    """For each decision timestamp, the latest value whose available_at <= decision.

    decisions: DatetimeIndex (UTC, any order); observed/available/values: aligned arrays.
    Returns a Series indexed like `decisions`.
    """
    left = pd.DataFrame({'decision': decisions, 'pos': range(len(decisions))}).sort_values('decision')
    right = pd.DataFrame({'available': available, name: values, 'obs_date': observed}).sort_values('available')
    merged = pd.merge_asof(left, right, left_on='decision', right_on='available', direction='backward')
    return merged.sort_values('pos')[name].to_numpy()


def kst(ts):
    return ts.astimezone(KST)
