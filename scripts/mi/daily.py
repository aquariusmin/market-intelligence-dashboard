"""Daily market intelligence: day-over-day changes, regime explanation, daily snapshots.

Change thresholds were fixed on 2026-10-15 before looking at any day-over-day output, and are not tuned to
data. Changing one means bumping METHOD so older snapshots stay comparable among themselves.

Every change carries two texts: `text` (with numbers, shown on screen) and `plain` (no price-derived
numbers), used for archived copies and LLM input of markets whose source may not be stored (FRED).
Day-over-day values are recomputed from the price history at each run (regime.analyze `previous`), so a
snapshot is an audit record of what was shown, never an input to later calculations.
"""
import hashlib
import json

METHOD = 'daily-v1'
RISK_POINTS = 10.0      # |risk score change| that is reported (same component set only)
TREND_POINTS = 2        # |trend score change| (-5..+5 scale)
VOL_LINE = 0.70         # 20-day volatility percentile line (= regime.VOL_HIGH_PCT)
UST10Y_BP = 10.0        # US 10y yield change vs the previous observation, basis points
USDKRW_PCT = 1.0        # USD/KRW change vs the previous observation, percent
THRESHOLDS = {'risk_points': RISK_POINTS, 'trend_points': TREND_POINTS, 'vol_line': VOL_LINE,
              'ust10y_bp': UST10Y_BP, 'usdkrw_pct': USDKRW_PCT}

REGIME_KO = {'Bullish / Low Volatility': '상승 추세 · 낮은 변동성', 'Bullish / High Volatility': '상승 추세 · 높은 변동성',
             'Bearish / Low Volatility': '하락 추세 · 낮은 변동성', 'Bearish / High Volatility': '하락 추세 · 높은 변동성',
             'Sideways / Neutral': '횡보 · 중립'}
RISK_KO = {'realized_vol': '실현변동성', 'drawdown': '낙폭', 'vix': 'VIX', 'credit': '신용스프레드', 'rates': '금리 변동',
           'krw': '원화'}


def _ko(label):
    return REGIME_KO.get(label, label or '—')


def _change(kind, text, plain=None):
    return {'kind': kind, 'text': text, 'plain': plain or text}


def current_state(r):
    return {'as_of': r['as_of'], 'regime': r['regime'], 'raw_regime': r['raw_regime'], 'trend_score': r['trend_score'],
            'vol20_pct': (r.get('metrics') or {}).get('vol20_pct'),
            'risk': {k: r['risk'].get(k) for k in ('score', 'level', 'level_ko', 'components')}}


def _available(components):
    return {k for k, v in (components or {}).items() if v is not None}


def diff(prev, cur, pending_days=0, confirm_days=3):
    """Changes between two comparable session states (regime._state shape). Pure, deterministic."""
    if not prev or not cur:
        return []
    out = []
    if prev['regime'] != cur['regime']:
        out.append(_change('regime', '국면 변화: %s → %s' % (_ko(prev['regime']), _ko(cur['regime']))))
    if cur['raw_regime'] != cur['regime'] and cur['raw_regime'] != prev.get('raw_regime'):
        out.append(_change('signal', '새 원시 신호: %s (전환 대기 %d/%d일)' % (_ko(cur['raw_regime']), pending_days,
                                                                     confirm_days)))
    pr, cr = prev['risk'], cur['risk']
    if pr.get('level') and cr.get('level') and pr['level'] != cr['level']:
        out.append(_change('risk_band', '위험 등급: %s → %s' % (pr['level_ko'], cr['level_ko'])))
    a, b = _available(pr.get('components')), _available(cr.get('components'))
    if a != b:
        parts = (['추가: ' + ', '.join(RISK_KO.get(k, k) for k in sorted(b - a))] if b - a else []) + \
                (['제외: ' + ', '.join(RISK_KO.get(k, k) for k in sorted(a - b))] if a - b else [])
        out.append(_change('risk_components', '위험 구성요소 변경(%s) · 점수 비교 불가' % ' / '.join(parts)))
    elif pr.get('score') is not None and cr.get('score') is not None and abs(round(cr['score'] - pr['score'], 6)) >= RISK_POINTS:
        d = cr['score'] - pr['score']
        out.append(_change('risk_score', '위험 점수 %.1f → %.1f (%+.1f)' % (pr['score'], cr['score'], d),
                           '위험 점수 %d점 이상 %s' % (RISK_POINTS, '상승' if d > 0 else '하락')))
    if prev.get('trend_score') is not None and cur.get('trend_score') is not None and \
            abs(cur['trend_score'] - prev['trend_score']) >= TREND_POINTS:
        d = cur['trend_score'] - prev['trend_score']
        out.append(_change('trend', '추세 점수 %+d → %+d' % (prev['trend_score'], cur['trend_score']),
                           '추세 점수 %s (%d단계 이상)' % ('개선' if d > 0 else '악화', TREND_POINTS)))
    pv, cv = prev.get('vol20_pct'), cur.get('vol20_pct')
    if pv is not None and cv is not None and (pv < VOL_LINE) != (cv < VOL_LINE):
        up = cv >= VOL_LINE
        out.append(_change('volatility', '20일 변동성 백분위 %.2f → %.2f (기준 %.2f %s)' % (pv, cv, VOL_LINE,
                                                                                 '상향 돌파' if up else '하향 이탈'),
                           '20일 변동성 백분위가 기준 %.2f을 %s' % (VOL_LINE, '상향 돌파' if up else '하향 이탈')))
    return out


def explanation(r):
    """Rule-by-rule account of the current regime (template text, no LLM)."""
    rules = ['%s %s%s' % ('✓' if t['pass'] else '✕', t['label'],
                          '' if t.get('value') is None else ' (%+.2f%%)' % t['value']) for t in r.get('trend_rules', [])]
    vb = r.get('vol_basis') or {}
    if vb.get('fallback_abs'):
        vol = '변동성 판정: 백분위 이력 부족 → 20일 변동성 절대 기준 %.0f%% 적용' % vb.get('abs_threshold', 20)
    elif vb.get('percentile') is not None:
        vol = '변동성 판정: 최근 1년 백분위 %.2f (기준 %.2f 이상이면 높은 변동성)' % (vb['percentile'], vb['threshold'])
    else:
        vol = '변동성 판정: 이용 불가'
    lines = ['추세 점수 %+d/5 = 통과 규칙 수 − 미통과 규칙 수 (상승 ≥ +3, 하락 ≤ −3, 그 외 횡보)' % r['trend_score'],
             ' · '.join(rules), vol,
             '현재 국면 %d거래일째 (%s부터)' % (r['regime_days'], r['regime_since'])]
    if r.get('pending_days'):
        lines.append('원시 신호 %s가 %d/%d일 연속 · %d일 연속이면 국면 전환' % (_ko(r['raw_regime']), r['pending_days'],
                                                               r['confirm_days'], r['confirm_days']))
    return lines


def market_block(r):
    """`daily` block added to regime.json markets[k] (screen). None when the market has no analysis."""
    if not r or r.get('status') != 'ok':
        return None
    prev = r.get('previous')
    return {'method': METHOD, 'previous_as_of': prev and prev['as_of'],
            'changes': diff(prev, current_state(r), r.get('pending_days', 0), r.get('confirm_days', 3)),
            'explanation': explanation(r)}


def macro_changes(series):
    """Changes of the latest macro observation vs the previous one (macro.json `series`)."""
    out = []
    rules = [('UST10Y', '미국 10년물 금리', lambda a, b: (b - a) * 100, UST10Y_BP, '%+.0fbp'),
             ('USDKRW', 'USD/KRW', lambda a, b: (b / a - 1) * 100, USDKRW_PCT, '%+.2f%%')]
    for sid, name, fn, limit, fmt in rules:
        h = (series.get(sid) or {}).get('history') or []
        if len(h) < 2 or h[-2]['value'] in (None, 0):
            continue
        d = round(fn(h[-2]['value'], h[-1]['value']), 6)   # 4.10 - 4.00 is 10bp, not 9.999…
        if abs(d) >= limit:
            out.append('%s 전 관측일 대비 %s (%s → %s)' % (name, fmt % d, h[-2]['date'], h[-1]['date']))
    return out


def snapshot(market, r, restricted=False):
    """Row for daily_snapshots. Restricted markets keep labels only (no price-derived numbers)."""
    block = r.get('daily') or market_block(r) or {}
    payload = {'regime': r['regime'], 'raw_regime': r['raw_regime'], 'risk_level': r['risk']['level'],
               'components_available': r['risk'].get('components_available'),
               'changes': [c['plain' if restricted else 'text'] for c in block.get('changes', [])]}
    if not restricted:
        payload.update(trend_score=r['trend_score'], risk_score=r['risk']['score'],
                       risk_components=r['risk']['components'], vol20_pct=(r.get('metrics') or {}).get('vol20_pct'),
                       pending_days=r.get('pending_days', 0))
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return {'market': market, 'as_of': r['as_of'], 'method': METHOD, 'source_id': r.get('source_id') or '',
            'regime': r['regime'], 'risk_level': r['risk']['level'], 'restricted': int(bool(restricted)),
            'payload': text, 'payload_hash': hashlib.sha256(text.encode()).hexdigest()[:16]}
