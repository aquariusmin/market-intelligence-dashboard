"""v0.6 outlook-change explanation (deterministic, no LLM).

Why did today's probability differ from the previous session's? The same fitted model scores the previous
session's inputs and today's inputs; the difference is split by reverting one input at a time to its
previous value (one-at-a-time attribution). Contributions need not sum exactly to the total because
models interact inputs; the remainder is reported as `interaction`.

These are changes in *model inputs*, not causes of market moves. Explanations are produced only for
models that passed validation: a reliable-looking story for an unreliable probability is worse than none.
"""
import math

import numpy as np

CLASSES = ('bearish', 'neutral', 'bullish')
TOP = 3
MIN_SHOWN_PP = 0.5   # contributions under half a percentage point are not listed

FEATURE_KO = {
    'ret1': '1일 수익률', 'ret5': '5일 수익률', 'ret20': '20일 수익률', 'ret60': '60일 수익률',
    'dist_sma20': '20일선 괴리', 'dist_sma50': '50일선 괴리', 'dist_sma200': '200일선 괴리', 'rsi14': 'RSI(14)',
    'macd_hist_pct': 'MACD 히스토그램', 'vol20': '20일 변동성', 'vol60': '60일 변동성', 'vol_ratio': '변동성 비율(20/60)',
    'drawdown252': '52주 고점 대비', 'ust10y': '미국 10년물 금리', 'ust2y': '미국 2년물 금리', 'ust3m': '미국 3개월물 금리',
    'curve_2s10s': '10년-2년 금리차', 'usdkrw_ret5': 'USD/KRW 5일 변화', 'usdkrw_ret20': 'USD/KRW 20일 변화',
    'usd_broad_ret20': '달러지수 20일 변화', 'ust10y_chg20': '10년물 20일 변화', 'ust2y_chg20': '2년물 20일 변화',
    'ust3m_chg20': '3개월물 20일 변화', 'vix': 'VIX', 'vix_chg5': 'VIX 5일 변화', 'hy_oas': '하이일드 스프레드',
    'hy_oas_chg20': '하이일드 스프레드 20일 변화', 'mom5': '5일 모멘텀', 'mom20': '20일 모멘텀', 'mom60': '60일 모멘텀',
    'corr60': '60일 수익률 상관',
}


def label(feature):
    names = {'sp': 'S&P 500', 'nq': 'NASDAQ 100', 'ks': 'KOSPI', 'kq': 'KOSDAQ'}
    if feature in FEATURE_KO:
        return FEATURE_KO[feature]
    if feature.startswith('rel_strength_'):
        return '%s 대비 상대강도' % names.get(feature[13:], feature[13:])
    head, _, tail = feature.partition('_')
    return '%s %s' % (names[head], FEATURE_KO.get(tail, tail)) if head in names else feature


def _num(v):
    return None if v is None or not math.isfinite(float(v)) else round(float(v), 4)


def attribute(predict, cur, prev):
    """predict: DataFrame(1 row) -> probability array [bear, neutral, bull]; cur/prev: 1-row DataFrames, same columns.
    Returns dict(total, contributions sorted by size, interaction) in probability units."""
    p_cur, p_prev = np.asarray(predict(cur))[0], np.asarray(predict(prev))[0]
    total = p_cur - p_prev
    rows = []
    for col in cur.columns:
        a, b = prev[col].iloc[0], cur[col].iloc[0]
        same = (a == b) or (isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b))
        if same:
            continue
        x = cur.copy()
        x[col] = prev[col].to_numpy()
        d = p_cur - np.asarray(predict(x))[0]          # what this input's change added to today's probability
        rows.append({'feature': col, 'label': label(col), 'from': _num(a), 'to': _num(b),
                     'd_bear': float(d[0]), 'd_neutral': float(d[1]), 'd_bull': float(d[2])})
    rows.sort(key=lambda r: -(abs(r['d_bull']) + abs(r['d_bear'])))
    explained = np.array([sum(r[k] for r in rows) for k in ('d_bear', 'd_neutral', 'd_bull')]) if rows else np.zeros(3)
    return {'p_prev': [float(v) for v in p_prev], 'p_cur': [float(v) for v in p_cur],
            'total': [float(v) for v in total], 'contributions': rows,
            'interaction': [float(v) for v in total - explained]}


def summary(att, prev_cutoff, cutoff, top=TOP):
    """JSON block for forecast.json: percentage points, top inputs, plain Korean lines."""
    pp = lambda v: round(100 * v, 1)
    top_rows = [r for r in att['contributions'] if max(abs(r['d_bull']), abs(r['d_bear'])) * 100 >= MIN_SHOWN_PP][:top]
    lines = ['상승 확률 %.0f%% → %.0f%% (%+.1f%%p), 하락 확률 %.0f%% → %.0f%% (%+.1f%%p) · %s → %s 입력 기준' % (
        100 * att['p_prev'][2], 100 * att['p_cur'][2], pp(att['total'][2]),
        100 * att['p_prev'][0], 100 * att['p_cur'][0], pp(att['total'][0]), prev_cutoff, cutoff)]
    for r in top_rows:
        lines.append('%s %s → %s: 상승 %+.1f%%p · 하락 %+.1f%%p' % (
            r['label'], '—' if r['from'] is None else '%.4g' % r['from'], '—' if r['to'] is None else '%.4g' % r['to'],
            pp(r['d_bull']), pp(r['d_bear'])))
    if not top_rows:
        lines.append('개별 입력 변화의 영향이 모두 0.5%p 미만입니다.')
    return {'method': 'one-at-a-time-revert-v1', 'previous_cutoff': prev_cutoff, 'cutoff': cutoff,
            'delta_pp': {c: pp(v) for c, v in zip(CLASSES, att['total'])},
            'top': [dict(r, d_bull_pp=pp(r['d_bull']), d_bear_pp=pp(r['d_bear'])) for r in top_rows],
            'interaction_pp': {c: pp(v) for c, v in zip(CLASSES, att['interaction'])},
            'lines': lines,
            'note': '같은 모델로 직전 거래일과 오늘의 입력을 비교해 확률 변화를 입력별로 나눈 값입니다. 시장 움직임의 원인이 아니며, '
                    '입력 간 상호작용 몫은 따로 표시합니다.'}
