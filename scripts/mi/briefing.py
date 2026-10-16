"""Korean daily briefing.

Rule-based by default: every sentence is generated from stored, sourced values.
Optional LLM rewrite (anthropic SDK) only with an explicit --llm flag AND ANTHROPIC_API_KEY (paid call).
Probabilities never come from the LLM: they are copied from the quantitative forecast records, and the LLM
output is rejected if it introduces a number not present in the supplied facts, flips a sign, attributes a
number to a different market, or uses forecast/advice vocabulary (validate_llm).
"""
import json
import os
import re
from datetime import datetime, timezone

from . import news, timing

NAMES = {'sp': 'S&P 500', 'nq': 'NASDAQ 100', 'ks': 'KOSPI', 'kq': 'KOSDAQ'}
REGIME_KO = {'Bullish / Low Volatility': '상승 추세 · 낮은 변동성', 'Bullish / High Volatility': '상승 추세 · 높은 변동성',
             'Bearish / Low Volatility': '하락 추세 · 낮은 변동성', 'Bearish / High Volatility': '하락 추세 · 높은 변동성',
             'Sideways / Neutral': '횡보 · 중립'}
LLM_MODEL = 'claude-opus-5-5'
LLM_MAX_TOKENS = 4000
# USD per million tokens (input, output), Anthropic list prices read 2026-10-16. A fallback may serve another model;
# unknown models are charged at the highest rate below so the budget check errs on the safe side.
LLM_PRICES = {'claude-opus-5-5': (4.0, 20.0), 'claude-opus-5': (5.0, 25.0), 'claude-opus-4-8': (5.0, 25.0),
              'claude-sonnet-5-5': (2.0, 10.0), 'claude-haiku-5-5': (0.10, 0.50)}
LLM_MONTHLY_BUDGET_USD = 3.5   # ≈ 5,000원/월 (user's ceiling, 2026-10-16); MI_LLM_BUDGET_USD overrides


def llm_cost(model, input_tokens, output_tokens):
    p_in, p_out = LLM_PRICES.get(model, max(LLM_PRICES.values()))
    return (input_tokens * p_in + output_tokens * p_out) / 1e6


def llm_budget():
    try:
        return float(os.environ.get('MI_LLM_BUDGET_USD') or LLM_MONTHLY_BUDGET_USD)
    except ValueError:
        return LLM_MONTHLY_BUDGET_USD


def _pct(v, d=2):
    return '—' if v is None else ('%+.' + str(d) + 'f%%') % v


def market_paragraph(key, r, restricted=False):
    if not r or r.get('status') != 'ok':
        reason = (r or {}).get('reason', '데이터 없음')
        return '%s: 분석 불가 (%s).' % (NAMES[key], reason)
    if restricted:
        return ('%s는 %s 기준 %s 국면이며 위험 수준은 %s입니다 (가격 파생 수치는 데이터 약관상 보관·외부 전송하지 않음).'
                % (NAMES[key], r['as_of'], REGIME_KO.get(r['regime'], r['regime']), r['risk']['level_ko']))
    m = r['metrics']
    return ('%s는 %s 기준 %s 국면입니다 (추세 점수 %+d/5, %s일째). 20거래일 수익률 %s, 60거래일 %s, '
            '20일 실현변동성 %s, 52주 고점 대비 %s. 위험 수준은 %s(%s점)입니다.'
            % (NAMES[key], r['as_of'], REGIME_KO.get(r['regime'], r['regime']), r['trend_score'], r['regime_days'],
               _pct(m['mom20']), _pct(m['mom60']),
               '이용 불가' if m.get('vol20') is None else '%.1f%%' % m['vol20'], _pct(m['drawdown252'], 1),
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


def forecast_explanations(forecast):
    """Why a validated model's probability changed since the previous session (mi.explain output)."""
    out = []
    for key in NAMES:
        for h, f in sorted(((forecast or {}).get('markets', {}).get(key) or {}).items(), key=lambda kv: int(kv[0])):
            lines = ((f or {}).get('explanation') or {}).get('lines') or []
            if lines:
                out.append('%s %s거래일 전망 변화(검증 통과 모델): %s' % (NAMES[key], h, lines[0]))
                out += ['  · 주요 입력: ' + x for x in lines[1:]]
    return out


def outlook_changes(regimes, prev_forecasts, restricted=()):
    out = []
    for key, r in regimes.items():
        if r and r.get('status') == 'ok' and r.get('daily'):   # thresholds fixed in mi.daily
            out += ['%s %s' % (NAMES[key], c['plain' if key in restricted else 'text']) for c in r['daily']['changes']]
            continue
        if not r or r.get('status') != 'ok' or len(r.get('history', [])) < 2:
            continue
        a, b = r['history'][-2], r['history'][-1]
        if a['regime'] != b['regime']:
            out.append('%s 국면 변화: %s → %s (%s)' % (NAMES[key], REGIME_KO.get(a['regime']), REGIME_KO.get(b['regime']),
                                                  b['date']))
        if a.get('risk') is not None and b.get('risk') is not None and abs(b['risk'] - a['risk']) >= 5:
            out.append('%s 위험 점수 변화 5점 이상 (수치 미보관)' % NAMES[key] if key in restricted else
                       '%s 위험 점수 %.0f → %.0f' % (NAMES[key], a['risk'], b['risk']))
    for row in prev_forecasts or []:
        out.append('%s %dD 상승 확률 %.0f%% → %.0f%% (%s → %s)' % (NAMES[row['market']], row['horizon'],
                                                              row['prev'] * 100, row['cur'] * 100,
                                                              row['prev_cutoff'], row['cur_cutoff']))
    return out or ['전일 대비 국면·위험 등급·예측 확률의 의미 있는 변화 없음 (또는 비교할 이전 기록 없음).']


def _withheld(ok, restricted, key):
    return ['%s · 요인 %d개 (가격 파생 수치라 보관하지 않음)' % (NAMES[k], len(r[key]))
            for k, r in ok.items() if k in restricted and r[key]]


def daily_summary(regimes, macro_changes=None, restricted=()):
    """Per market: regime, risk, number of day-over-day changes; then macro changes. Built only from mi.daily output."""
    out = []
    for key in NAMES:
        r = regimes.get(key)
        if not r or r.get('status') != 'ok':
            continue
        d = r.get('daily') or {}
        days = '' if key in restricted else ' %d거래일째' % r['regime_days']
        score = '' if key in restricted or r['risk']['score'] is None else ' %.1f점' % r['risk']['score']
        n = len(d.get('changes', []))
        out.append('%s: %s%s · 위험 %s%s · 전일(%s) 대비 %s' % (
            NAMES[key], REGIME_KO.get(r['regime'], r['regime']), days, r['risk']['level_ko'], score,
            d.get('previous_as_of') or '—', '변화 %d건 (Outlook Changes 참고)' % n if n else '기준 이상 변화 없음'))
    if macro_changes is not None:
        out.append('거시: ' + ('; '.join(macro_changes) if macro_changes else '전 관측일 대비 기준 이상 변화 없음'))
    return out


def build(regimes, macro, forecast, news_items, events, prev_forecasts=None, session=None, now=None, restricted=(),
          macro_changes=None):
    """`restricted`: markets whose price source may not be stored (licenses.storage_allowed). Their price-derived
    numbers are replaced by labels; this variant is what goes to SQLite and to the optional LLM."""
    now = now or datetime.now(timezone.utc)
    session = session or ('morning' if timing.kst(now).hour < 12 else 'evening')
    ok = {k: r for k, r in regimes.items() if r and r.get('status') == 'ok'}
    bull = [(k, f) for k, r in ok.items() if k not in restricted for f in r['bullish_factors']]
    bear = [(k, f) for k, r in ok.items() if k not in restricted for f in r['bearish_factors']]
    risk_sorted = sorted(ok.items(), key=lambda kv: -(kv[1]['risk']['score'] or 0))
    summary = []
    if ok:
        summary.append('분석 가능한 시장: %s.' % ', '.join('%s(%s)' % (NAMES[k], REGIME_KO.get(r['regime'])) for k, r in ok.items()))
        hi = risk_sorted[0]
        summary.append('위험 점수가 가장 높은 시장은 %s(%s%s)입니다.' % (
            NAMES[hi[0]], hi[1]['risk']['level_ko'], '' if hi[0] in restricted else ', %s점' % hi[1]['risk']['score']))
    missing = [NAMES[k] for k in NAMES if k not in ok]
    if missing:
        summary.append('%s는 데이터가 없어 분석에서 제외했습니다.' % ', '.join(missing))
    ms = macro_sentences(macro)
    if ms:
        summary.append('거시 지표: ' + ', '.join(ms[:3]) + '.')
    summary.append('방향 확률은 라이선스·검증 조건을 충족한 정량 모델에서만 제공하며, 국면 분류는 예측이 아닙니다.')
    # importance is re-derived at read time (age penalty relative to `now`, not to news.json generation time)
    imp = lambda n: news.effective_importance(n.get('base_importance', n.get('importance', 0)), n.get('published_at'), now)
    news_items = [dict(n, importance=imp(n)) for n in news_items]
    newest_first = sorted(news_items, key=lambda n: n.get('published_at') or n.get('collected_at') or '', reverse=True)
    top_news = sorted([n for n in newest_first if n['importance'] >= 3], key=lambda n: -n['importance'])[:6]
    sources = [{'label': '가격: %s' % NAMES[k], 'detail': '%s · 기준일 %s' % (r.get('source_id', ''), r['as_of'])}
               for k, r in ok.items()]
    sources += [{'label': v['name'], 'detail': '%s · %s' % (v['source_id'], v['as_of'])} for v in macro.values()
                if 'source_id' in v]
    return {
        'generated_at': now.isoformat(timespec='seconds'), 'session': session, 'method': 'rule-based-v1',
        'sections': {
            'executive_summary': ' '.join(summary[:5]),
            'daily_summary': daily_summary(regimes, macro_changes, restricted),
            'us_overview': [market_paragraph(k, regimes.get(k), k in restricted) for k in ('sp', 'nq')],
            'korea_overview': [market_paragraph(k, regimes.get(k), k in restricted) for k in ('ks', 'kq')] +
                              [s for s in ms if 'USD/KRW' in s],
            'bullish_factors': (['%s · %s' % (NAMES[k], f) for k, f in bull][:8] + _withheld(ok, restricted, 'bullish_factors')
                                or ['뚜렷한 상승 요인 없음']),
            'bearish_factors': (['%s · %s' % (NAMES[k], f) for k, f in bear][:8] + _withheld(ok, restricted, 'bearish_factors')
                                or ['뚜렷한 위험 요인 없음']),
            'market_forecast': forecast_lines(forecast),
            'outlook_changes': outlook_changes(regimes, prev_forecasts, restricted) + forecast_explanations(forecast),
            'events_to_watch': [{'date': e['date'], 'title': e['title'], 'url': e.get('url'), 'source': e['source']}
                                for e in (events or {}).get('items', [])[:8]],
            'news': [{'title': n['title'], 'url': n['url'], 'source': n['source'], 'category': n['category'],
                      'importance': n['importance']} for n in top_news],
        },
        'sources': sources,
        'disclaimer': '개인 참고용 자동 생성 브리핑입니다. 투자 권유가 아니며, 국면·지표는 현재 상태의 서술입니다.',
        **({'redacted_markets': sorted(restricted)} if restricted else {}),
    }


# ---------------- optional LLM rewrite (explicit opt-in, paid) ----------------

MARKET_ALIASES = {'sp': r'S&P\s?500|S&P|SP500|에스앤피', 'nq': r'NASDAQ\s?100|NASDAQ|나스닥\s?100|나스닥',
                  'ks': r'KOSPI|코스피', 'kq': r'KOSDAQ|코스닥'}
_TOKEN = re.compile('|'.join('(?P<%s>%s)' % (k, v) for k, v in MARKET_ALIASES.items()) +
                    r'|(?<![0-9A-Za-z.])(?P<sign>[+-]?)(?P<num>\d+(?:\.\d+)?)', re.I)
_SENTENCE = re.compile(r'(?<!\d)[.!?](?!\d)|\n')
# Predictive / advisory vocabulary is never allowed in the rewrite (the rule-based facts contain none).
FORBIDDEN = re.compile(r'할 것|될 것|일 것|갈 것|것으로 (보|예상|예측|전망)|보일 것|전망|예상|상승할|하락할|오를|떨어질|'
                       r'반등할|급등할|급락할|매수|매도|추천|유망|\bwill\b|\bexpect|\bbuy\b|\bsell\b', re.I)
MULTIPLIERS = re.compile(r'두 ?배|세 ?배|네 ?배|몇 ?배|절반|반토막|배로|배 (증가|확대|감소)')


def _tokens(text):
    """[(sentence_no, kind, value)] with kind 'm' (market key) or 'n' ((magnitude, negative?))."""
    out = []
    for no, sent in enumerate(_SENTENCE.split(text.replace('\u2212', '-'))):
        for t in _TOKEN.finditer(sent):
            if t.group('num') is not None:
                out.append((no, 'n', (float(t.group('num')), t.group('sign') == '-')))
            else:
                out.append((no, 'm', next(k for k in MARKET_ALIASES if t.group(k))))
    return out


def _fact_numbers(facts_text):
    """{(magnitude, negative?): [set of markets the number is stated for (empty = not market-specific)]}."""
    try:
        data = json.loads(facts_text)
    except ValueError:
        data = facts_text
    segments = []
    walk = lambda x: (segments.append(x) if isinstance(x, str) else
                      [walk(v) for v in (x.values() if isinstance(x, dict) else x if isinstance(x, list) else [])])
    walk(data)
    out = {}
    for seg in segments:
        toks = _tokens(seg)
        lead = toks[0][2] if toks and toks[0][1] == 'm' and _TOKEN.match(seg.strip()) else None  # paragraph subject
        for no, kind, val in toks:
            if kind == 'n':
                ms = {v for n, k, v in toks if n == no and k == 'm'} or ({lead} if lead else set())
                out.setdefault(val, []).append(ms)
    return out


def validate_llm(text_obj, facts_text):
    """LLM output must be {"executive_summary": str}, introduce no new numbers, keep each number's sign and the
    market it is attributed to in FACTS, and contain no forecast/advice vocabulary or unstated multipliers."""
    if not isinstance(text_obj, dict) or set(text_obj) != {'executive_summary'} \
            or not isinstance(text_obj['executive_summary'], str) or not 20 <= len(text_obj['executive_summary']) <= 1200:
        raise ValueError('LLM 출력 스키마 불일치')
    text = text_obj['executive_summary']
    bad = FORBIDDEN.search(text)
    if bad:
        raise ValueError('LLM 출력에 전망·권유 표현 포함: %s' % bad.group(0))
    mult = MULTIPLIERS.search(text)
    if mult and mult.group(0) not in facts_text:
        raise ValueError('LLM 출력에 입력에 없는 배수 표현 포함: %s' % mult.group(0))
    facts = _fact_numbers(facts_text)
    magnitudes = {m for m, _ in facts}
    new, flipped, moved = [], [], []
    market = {}
    for no, kind, val in _tokens(text):
        if kind == 'm':
            market[no] = val  # last market named so far in this sentence
            continue
        label = ('-' if val[1] else '') + ('%g' % val[0])
        if val not in facts:
            (flipped if val[0] in magnitudes else new).append(label)
        elif market.get(no) and not any(not ms or market[no] in ms for ms in facts[val]):
            moved.append('%s(%s)' % (label, market[no]))
    if new:
        raise ValueError('LLM이 입력에 없는 수치를 생성함: %s' % ', '.join(sorted(new)[:5]))
    if flipped:
        raise ValueError('LLM 출력의 수치 부호가 입력과 다름: %s' % ', '.join(flipped[:5]))
    if moved:
        raise ValueError('LLM 출력이 수치를 다른 시장에 귀속함: %s' % ', '.join(moved[:5]))
    return text_obj


def llm_rewrite(brief, client=None, facts_brief=None, ledger=None):
    """Rewrite only the executive summary. Returns (brief, status). Never raises.
    `facts_brief` (default: `brief`) is what the model sees: pass the restricted build so numbers derived from
    sources that may not be stored or redistributed never leave the machine.
    `ledger` (store.LlmLedger): when given, the call is skipped if this month's spend plus a worst-case estimate of
    this call would exceed the monthly budget, and the actual token usage is recorded after the call."""
    if client is None and not os.environ.get('ANTHROPIC_API_KEY'):
        return brief, 'skipped: ANTHROPIC_API_KEY 없음'
    facts = json.dumps({k: (facts_brief or brief)['sections'][k] for k in ('executive_summary', 'us_overview', 'korea_overview',
                                                           'bullish_factors', 'bearish_factors', 'market_forecast')},
                       ensure_ascii=False)
    system = ('당신은 금융시장 브리핑 편집자입니다. 주어진 FACTS JSON의 내용만 사용해 한국어 요약 3~5문장을 작성하세요. '
              '새로운 수치·확률·전망·사실을 추가하지 말고, 투자 권유를 하지 마세요. '
              '출력은 {"executive_summary": "..."} 형태의 JSON 하나만 작성하세요.')
    budget, spent = llm_budget(), (ledger.spent() if ledger else 0.0)
    # worst case: one token per character of input (Korean is denser than that) and the full output allowance
    worst = llm_cost(LLM_MODEL, len(facts) + len(system) + 200, LLM_MAX_TOKENS)
    if ledger and spent + worst > budget:
        return brief, 'skipped: 월 예산 $%.2f 중 $%.2f 사용 · 이번 호출 최대 $%.3f' % (budget, spent, worst)
    resp = None
    try:
        if client is None:
            import anthropic  # optional dependency: pip install anthropic
            client = anthropic.Anthropic()
        resp = client.beta.messages.create(
            model=LLM_MODEL, max_tokens=LLM_MAX_TOKENS, output_config={'effort': 'low'},
            betas=['server-side-fallback-2026-07-01'], fallbacks='default',
            system=system, messages=[{'role': 'user', 'content': 'FACTS:\n' + facts}])
        if resp.stop_reason == 'refusal':
            status = 'fallback: 모델 거절'
        else:
            text = next((b.text for b in resp.content if b.type == 'text'), '')
            obj = validate_llm(json.loads(text[text.find('{'): text.rfind('}') + 1]), facts)
            status = 'ok'
    except Exception as exc:  # any failure -> keep the rule-based text
        status = 'fallback: %s' % str(exc)[:200]
    cost = _record(ledger, resp, status)
    if cost is not None:
        status += ' · $%.4f (이번 달 $%.2f / $%.2f)' % (cost, spent + cost, budget)
    if not status.startswith('ok'):
        return brief, status
    out = json.loads(json.dumps(brief))
    out['sections']['executive_summary'] = obj['executive_summary']
    out['method'] = 'rule-based-v1 + llm-summary (%s)' % LLM_MODEL
    return out, status


def _record(ledger, resp, status):
    """Book the tokens a response was billed for; returns the cost, or None when there was no response."""
    usage = getattr(resp, 'usage', None)
    if resp is None or usage is None:
        return None
    model = getattr(resp, 'model', None) or LLM_MODEL
    tin, tout = int(getattr(usage, 'input_tokens', 0) or 0), int(getattr(usage, 'output_tokens', 0) or 0)
    cost = llm_cost(model, tin, tout)
    if ledger:
        ledger.record(model, tin, tout, cost, status)
    return cost
