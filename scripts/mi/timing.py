"""Point-in-time availability rules.

Every observation gets an `available_at` UTC timestamp; every forecast has a
`decision_time` UTC timestamp. A feature may be used only if available_at <= decision_time.
zoneinfo handles US daylight saving time (16:00 ET = 05:00 KST in summer, 06:00 KST in winter).
"""
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
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
# Daily index closes whose publication time is documented or conservatively assumed, per source.
# KRX OPEN API daily index data is treated as available the next weekday 08:00 KST (conservative).
# A user-licensed source must state `publication_delay_minutes` in its manifest entry; otherwise the
# close is assumed known only at 09:00 exchange-local time on the next business day.
PRICE_NEXT_DAY = {'krx_openapi': time(8, 0)}
UNSTATED_LICENSED_NEXT_DAY = time(9, 0)

# Conservative publication lags for macro series (series date d -> first time we could know it).
# H.15 daily yields: posted on the following business day (DDP) -> d+1 17:00 ET.
# H.10 daily FX: released weekly, normally Monday 16:15 ET for the prior week -> Tuesday after week end.
# FRED-hosted Cboe/ICE series: posted the following morning -> d+1 18:00 ET (conservative).
MACRO_LAG = {
    'next_business_day_17et': lambda d: _local(_next_weekday(d), time(17, 0), ET),
    # Monday release (Tuesday when Monday is a holiday) -> earliest Tuesday 16:15 ET, later if that is a holiday.
    'weekly_h10': lambda d: _local(_h10_release(d), time(16, 15), ET),
    'next_day_18et': lambda d: _local(_next_weekday(d), time(18, 0), ET),
}


def _nth_weekday(year, month, weekday, n):
    """n-th (1-based) weekday of a month; n=-1 = last."""
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


@lru_cache(maxsize=None)
def fed_holidays(year):
    """Federal Reserve Board holidays. A fixed-date holiday on Sunday is observed Monday; one on Saturday is
    treated as closing the Friday before (federal observance; conservative for the Fed, which may stay open)."""
    fixed = [date(year, 1, 1), date(year, 7, 4), date(year, 11, 11), date(year, 12, 25)]
    if year >= 2021:
        fixed.append(date(year, 6, 19))
    out = {_nth_weekday(year, 1, 0, 3), _nth_weekday(year, 2, 0, 3), _nth_weekday(year, 5, 0, -1),
           _nth_weekday(year, 9, 0, 1), _nth_weekday(year, 10, 0, 2), _nth_weekday(year, 11, 3, 4)}
    for d in fixed:
        out.add(d)
        if d.weekday() == 5:
            out.add(d - timedelta(days=1))
        elif d.weekday() == 6:
            out.add(d + timedelta(days=1))
    return frozenset(out)


def is_us_business_day(d):
    return d.weekday() < 5 and d not in fed_holidays(d.year)


def _next_weekday(d):
    """Next US federal business day after d."""
    d = d + timedelta(days=1)
    while not is_us_business_day(d):
        d += timedelta(days=1)
    return d


def _next_plain_weekday(d):
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _h10_release(d):
    monday = d + timedelta(days=7 - d.weekday())
    tuesday = monday + timedelta(days=1)
    release = monday if is_us_business_day(monday) else _next_weekday(monday)
    while release < tuesday or not is_us_business_day(release):
        release += timedelta(days=1)
    return release


def _local(d, t, tz):
    return datetime.combine(d, t, tz).astimezone(timezone.utc)


def close_utc(market, d):
    tz, t = SESSIONS[market]
    return _local(d, t, tz)


def price_available_at(market, dates, source_id=None, manifest=None):
    """dates: iterable of datetime.date -> pandas UTC timestamps, using the source's publication rule."""
    tz = SESSIONS[market][0]
    if source_id in PRICE_NEXT_DAY:
        at = PRICE_NEXT_DAY[source_id]
        return pd.to_datetime([_local(_next_plain_weekday(d), at, tz) for d in dates], utc=True)
    if source_id and source_id.startswith('licensed:'):
        entry = (manifest or {}).get(source_id.split(':', 1)[1]) or {}
        delay = entry.get('publication_delay_minutes')
        if isinstance(delay, (int, float)) and not isinstance(delay, bool) and delay >= 0:
            return pd.to_datetime([close_utc(market, d) + timedelta(minutes=delay) for d in dates], utc=True)
        return pd.to_datetime([_local(_next_plain_weekday(d), UNSTATED_LICENSED_NEXT_DAY, tz) for d in dates],
                              utc=True)
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
    # one timestamp resolution on both sides: pandas infers [s] for empty or second-precision input and [us]
    # elsewhere, and merge_asof refuses to compare different resolutions
    unit = 'datetime64[ns, UTC]'
    left = pd.DataFrame({'decision': pd.DatetimeIndex(decisions).astype(unit), 'pos': range(len(decisions))}
                        ).sort_values('decision')
    # Ties (e.g. a whole H.10 week released at once) resolve to the latest observation date, deterministically.
    right = pd.DataFrame({'available': pd.DatetimeIndex(available).astype(unit), name: values,
                          'obs_date': pd.to_datetime(list(observed))}).sort_values(['available', 'obs_date'], kind='mergesort')
    merged = pd.merge_asof(left, right, left_on='decision', right_on='available', direction='backward')
    return merged.sort_values('pos')[name].to_numpy()


def kst(ts):
    return ts.astimezone(KST)
