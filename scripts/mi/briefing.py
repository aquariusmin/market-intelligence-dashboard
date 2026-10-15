"""v0.8 Korean daily briefing.

Rule-based by default: every sentence is generated from stored, sourced values.
Optional LLM rewrite (anthropic SDK) only with an explicit --llm flag AND ANTHROPIC_API_KEY (paid call).
Probabilities never come from the LLM: they are copied from the quantitative forecast records, and the LLM
output is rejected if it introduces any number that is not present in the supplied facts.
"""
import json
import os
import re
from datetime import datetime, timezone

from . import timing

NAMES = {'sp': 'S&P 500', 'nq': 'NASDAQ 100', 'ks': 'KOSPI', 'kq': 'KOSDAQ'}
REGIME_KO = {'Bullish / Low Volatility': '상승 추세 · 낮은 변동성', 'Bullish / High Volatility': '상승 추세 · 높은 변동성',
             'Bearish / Low Volatility': '하락 추세 · 낮은 변동성', 'Bearish / High Volatility': '하락 추세 · 높은 변동성',
             'Sideways / Neutral': '횡보 · 중립'}
LLM_MODEL = 'claude-opus-5-5'


def _pct(v, d=2):
    return '—' if v is None else ('%+.' + str(d) + 'f%%') % v


def market_paragraph(key, r):
    if not r or r.get('status') != 'ok':
        reason = (r or {}).get('reason', '데이터 없음')
        return '%s: 분석 불가 (%s).' % (NAMES[key], reason)
    m = r['metrics']
    return ('%s는 %s 기준 %s 국면입니다 (추세 점수 %+d/5, %s일째). 20거래일 수익률 %s, 60거래일 %s, '
            '20일 실현변동성 %.1f%%, 52주 고점 대비 %s. 위험 수준은 %s(%s점)입니다.'
            % (NAMES[key], r['as_of'], REGIME_KO.get(r['regime'], r['regime']), r['trend_score'], r['regime_days'],
               _pct(m['mom20']), _pct(m['mom60']), m['vol20'] or 0, _pct(m['drawdown252'], 1),
               r['risk']['level_ko'], r['risk']['score']))


def macro_sentences(macro):
    out = []
    t = macro.get('UST10Y')
    if t:
        out.append('미국 10년물 금리 %.2f%% (20일 %+.0fbp, %s 기준)' % (t['value'], (t.get('chg20') or 0) * 100, t['as_of']))
    c = macro.get('CURVE_2S10S')
    if c:
        out.append('10년-2년 금리차 %+.2f%%p' % c['value'])
    k = macro.get('USDKRW')
    if k:
        chg = k.get('chg20')
        ret = None if chg is None else chg / (k['value'] - chg) * 100
        out.append('USD/KRW %.1f원 (20일 %s, %s 기준 · 주간 게시)' % (k['value'], _pct(ret), k['as_of']))
    v = macro.get('VIX')
    if v:
        out.append('VIX %.1f (%s 기준)' % (v['value'], v['as_of']))
    return out


def forecast_lines(forecast):
    lines = []
    for key in NAMES:
        f = ((forecast or {}).get('markets', {}).get(key) or {}).get('5') or {}
        if f.get('status') in ('validated', 'not_validated'):
            tag = '검증 통과' if f['status'] == 'validated' else '미검증 · 참고 불가'
            lines.append('%s 5거래일: 상승 %.0f%% / 횡보 %.0f%% / 하락 %.0f%% (%s, %s)'
                         % (NAMES[key], f['bullish_probability'] * 100, f['neutral_probability'] * 100,
                            f['bearish_probability'] * 100, f['model_name'], tag))
        else:
            lines.append('%s: 예측 제공 불가 — %s' % (NAMES[key], f.get('reason', '모델 미학습')))
    return lines


def outlook_changes(regimes, prev_forecasts):
    out = []
    for key, r in regimes.items():
        if not r or r.get('status') != 'ok' or len(r.get('history', [])) < 2:
            continue
        a, b = r['history'][-2], r['history'][-1]
        if a['regime'] != b['regime']:
            out.append('%s 국면 변화: %s → %s (%s)' % (NAMES[key], REGIME_KO.get(a['regime']), REGIME_KO.get(b['regime']),
                                                  b['date']))
        if a.get('risk') is not None and b.get('risk') is not None and abs(b['risk'] - a['risk']) >= 5:
            out.append('%s 위험 점수 %.0f → %.0f' % (NAMES[key], a['risk'], b['risk']))
    for row in prev_forecasts or []:
        out.append('%s %dD 상승 확률 %.0f%% → %.0f%% (%s → %s)' % (NAMES[row['market']], row['horizon'],
                                                              row['prev'] * 100, row['cur'] * 100,
                                                              row['prev_cutoff'], row['cur_cutoff']))
    return out or ['전일 대비 국면·위험 등급·예측 확률의 의미 있는 변화 없음 (또는 비교할 이전 기록 없음).']


def build(regimes, macro, forecast, news_items, events, prev_forecasts=None, session=None, now=None):
    now = now or datetime.now(timezone.utc)
    session = session or ('morning' if timing.kst(now).hour < 12 else 'evening')
    ok = {k: r for k, r in regimes.items() if r and r.get('status') == 'ok'}
    bull = [(k, f) for k, r in ok.items() for f in r['bullish_factors']]
    bear = [(k, f) for k, r in ok.items() for f in r['bearish_factors']]
    risk_sorted = sorted(ok.items(), key=lambda kv: -(kv[1]['risk']['score'] or 0))
    summary = []
    if ok:
        summary.append('분석 가능한 시장: %s.' % ', '.join('%s(%s)' % (NAMES[k], REGIME_KO.get(r['regime'])) for k, r in ok.items()))
        hi = risk_sorted[0]
        summary.append('위험 점수가 가장 높은 시장은 %s(%s, %s점)입니다.' % (NAMES[hi[0]], hi[1]['risk']['level_ko'], hi[1]['risk']['score']))
    missing = [NAMES[k] for k in NAMES if k not in ok]
    if missing:
        summary.append('%s는 데이터가 없어 분석에서 제외했습니다.' % ', '.join(missing))
    ms = macro_sentences(macro)
    if ms:
        summary.append('거시 지표: ' + ', '.join(ms[:3]) + '.')
    summary.append('방향 확률은 라이선스·검증 조건을 충족한 정량 모델에서만 제공하며, 국면 분류는 예측이 아닙니다.')
    top_news = sorted([n for n in news_items if n.get('importance', 0) >= 3],
                      key=lambda n: (-n['importance'], n.get('published_at') or ''))[:6]
    sources = [{'label': '가격: %s' % NAMES[k], 'detail': '%s · 기준일 %s' % (r.get('source_id', ''), r['as_of'])}
               for k, r in ok.items()]
    sources += [{'label': v['name'], 'detail': '%s · %s' % (v['source_id'], v['as_of'])} for v in macro.values()
                if 'source_id' in v]
    return {
        'generated_at': now.isoformat(timespec='seconds'), 'session': session, 'method': 'rule-based-v1',
        'sections': {
            'executive_summary': ' '.join(summary[:5]),
            'us_overview': [market_paragraph(k, regimes.get(k)) for k in ('sp', 'nq')],
            'korea_overview': [market_paragraph(k, regimes.get(k)) for k in ('ks', 'kq')] +
                              [s for s in ms if 'USD/KRW' in s],
            'bullish_factors': ['%s · %s' % (NAMES[k], f) for k, f in bull][:8] or ['뚜렷한 상승 요인 없음'],
            'bearish_factors': ['%s · %s' % (NAMES[k], f) for k, f in bear][:8] or ['뚜렷한 위험 요인 없음'],
            'market_forecast': forecast_lines(forecast),
            'outlook_changes': outlook_changes(regimes, prev_forecasts),
            'events_to_watch': [{'date': e['date'], 'title': e['title'], 'url': e.get('url'), 'source': e['source']}
                                for e in (events or {}).get('items', [])[:8]],
            'news': [{'title': n['title'], 'url': n['url'], 'source': n['source'], 'category': n['category'],
                      'importance': n['importance']} for n in top_news],
        },
        'sources': sources,
        'disclaimer': '개인 참고용 자동 생성 브리핑입니다. 투자 권유가 아니며, 국면·지표는 현재 상태의 서술입니다.',
    }


# ---------------- optional LLM rewrite (explicit opt-in, paid) ----------------

def _numbers(text):
    return set(re.findall(r'\d+(?:\.\d+)?', text))


def validate_llm(text_obj, facts_text):
    """LLM output must be {"executive_summary": str} and introduce no new numbers."""
    if not isinstance(text_obj, dict) or set(text_obj) != {'executive_summary'} \
            or not isinstance(text_obj['executive_summary'], str) or not 20 <= len(text_obj['executive_summary']) <= 1200:
        raise ValueError('LLM 출력 스키마 불일치')
    new = _numbers(text_obj['executive_summary']) - _numbers(facts_text)
    if new:
        raise ValueError('LLM이 입력에 없는 수치를 생성함: %s' % ', '.join(sorted(new)[:5]))
    return text_obj


def llm_rewrite(brief, client=None):
    """Rewrite only the executive summary. Returns (brief, status). Never raises."""
    if client is None and not os.environ.get('ANTHROPIC_API_KEY'):
        return brief, 'skipped: ANTHROPIC_API_KEY 없음'
    facts = json.dumps({k: brief['sections'][k] for k in ('executive_summary', 'us_overview', 'korea_overview',
                                                           'bullish_factors', 'bearish_factors', 'market_forecast')},
                       ensure_ascii=False)
    try:
        if client is None:
            import anthropic  # optional dependency: pip install anthropic
            client = anthropic.Anthropic()
        resp = client.beta.messages.create(
            model=LLM_MODEL, max_tokens=4000, output_config={'effort': 'low'},
            betas=['server-side-fallback-2026-07-01'], fallbacks='default',
            system=('당신은 금융시장 브리핑 편집자입니다. 주어진 FACTS JSON의 내용만 사용해 한국어 요약 3~5문장을 작성하세요. '
                    '새로운 수치·확률·전망·사실을 추가하지 말고, 투자 권유를 하지 마세요. '
                    '출력은 {"executive_summary": "..."} 형태의 JSON 하나만 작성하세요.'),
            messages=[{'role': 'user', 'content': 'FACTS:\n' + facts}])
        if resp.stop_reason == 'refusal':
            return brief, 'fallback: 모델 거절'
        text = next((b.text for b in resp.content if b.type == 'text'), '')
        obj = validate_llm(json.loads(text[text.find('{'): text.rfind('}') + 1]), facts)
    except Exception as exc:  # any failure -> keep the rule-based text
        return brief, 'fallback: %s' % str(exc)[:200]
    out = json.loads(json.dumps(brief))
    out['sections']['executive_summary'] = obj['executive_summary']
    out['method'] = 'rule-based-v1 + llm-summary (%s)' % LLM_MODEL
    return out, 'ok'
