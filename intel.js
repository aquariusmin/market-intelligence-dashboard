/* v1.0 REAL DATA views: regime, forecast, validation, news, briefing, system status.
   Reads data/dashboard/*.json written by scripts/mi_cli.py. Every panel states its source, as-of time and
   status; missing or unlicensed results are shown as unavailable, never replaced with sample values. */
const intelFiles = ['regime', 'macro', 'forecast', 'performance', 'news', 'briefing', 'status'];
let intel = {}, intelErrors = {};
// generated_at older than this many days -> STALE badge (performance is retrained rarely, so no threshold)
const STALE_DAYS = {regime: 3, forecast: 3, briefing: 3, news: 3, status: 3, macro: 7};
const isObj = v => v !== null && typeof v === 'object' && !Array.isArray(v);
// minimal per-file shape check; a file that fails it is not loaded (the last good copy, if any, is kept)
const INTEL_SHAPE = {regime: d => isObj(d.markets), macro: d => isObj(d.series), forecast: d => isObj(d.markets),
  performance: d => isObj(d.markets), news: d => Array.isArray(d.items), briefing: d => isObj(d.sections) && Array.isArray(d.sources),
  status: d => isObj(d.prices)};
const FORMAT_ERR = '형식 오류';
Object.assign(state, {range: '6M', newsCat: 'All', newsMarket: 'all', perfModel: null});
const H = [1, 5, 20];
const horizon = () => H[state.horizon];
const pct = (v, d = 1) => Number.isFinite(v) ? (v * 100).toFixed(d) + '%' : '—';
const num = (v, d = 2, suffix = '') => Number.isFinite(v) ? v.toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d}) + suffix : '—';
const sgn = (v, d = 2, suffix = '') => Number.isFinite(v) ? (v > 0 ? '+' : '') + v.toFixed(d) + suffix : '—';
const safeUrl = u => /^https?:\/\//i.test(u || '') ? esc(u) : '#';
const link = (u, text) => `<a href="${safeUrl(u)}" target="_blank" rel="noopener noreferrer" style="color:var(--blue)">${esc(text)}</a>`;
const pill = (text, kind) => `<span class="statuspill ${kind || ''}">${esc(text)}</span>`;
const REG_COLOR = {'Bullish / Low Volatility': '#55d9ad', 'Bullish / High Volatility': '#2f9c7c', 'Bearish / Low Volatility': '#c9757d',
  'Bearish / High Volatility': '#f18891', 'Sideways / Neutral': '#e6c17b'};
const REG_KO = {'Bullish / Low Volatility': '상승 · 저변동', 'Bullish / High Volatility': '상승 · 고변동', 'Bearish / Low Volatility': '하락 · 저변동',
  'Bearish / High Volatility': '하락 · 고변동', 'Sideways / Neutral': '횡보 · 중립'};
const regTone = r => !r ? 'neutral' : r.startsWith('Bullish') ? 'positive' : r.startsWith('Bearish') ? 'negative' : 'neutral';
const MODEL_KO = {class_frequency: '과거 클래스 빈도 (기준)', momentum_rule: '모멘텀 규칙 (기준)', logistic_regression: '로지스틱 회귀 (기준)', lightgbm: 'LightGBM'};
const statusKind = s => ({ok: 'ok', validated: 'ok', processed: 'ok', ok_transient: 'warn', stale: 'warn', partial: 'warn', not_validated: 'warn',
  running: 'warn', disabled: '', unavailable: 'bad', failed: 'bad', insufficient_history: 'bad'}[s] || '');

async function loadIntel() {
  if (location.protocol === 'file:') return;
  await Promise.all(intelFiles.map(async name => {
    try {
      const r = await fetch(`data/dashboard/${name}.json`, {cache: 'no-store'});
      if (!r.ok) throw Error(`HTTP ${r.status}`);
      const d = await r.json();
      if (!isObj(d) || !INTEL_SHAPE[name](d)) throw Error(`${FORMAT_ERR}: 예상한 키가 없습니다`);
      intel[name] = d;
      delete intelErrors[name];
    } catch (e) { intelErrors[name] = e.message; }  // keep the last good copy, show the error
  }));
}
const baseLoad = loadRealData;
loadRealData = async function () { await Promise.all([baseLoad(), loadIntel()]); render(); };

function missing(name, what) {
  return intel[name] ? '' : `<div class="note neutral">${esc(what)} ${intelErrors[name]?.startsWith(FORMAT_ERR) ? '파일 형식 오류' : '파일 없음'}${intelErrors[name] ? ' (' + esc(intelErrors[name]) + ')' : ''} · <code>.venv/bin/python scripts/mi_cli.py ${name === 'performance' ? 'train' : name === 'macro' ? 'regime' : name}</code> 실행 후 JSON 다시 읽기</div>`;
}
// header badges: reload failed while an older copy is still shown, and generated_at older than STALE_DAYS
function flags(name) {
  const out = [], d = intel[name], days = STALE_DAYS[name];
  if (d && intelErrors[name]) out.push(`<span class="statuspill bad" title="${esc(intelErrors[name])}">재로드 실패 · 이전 데이터</span>`);
  const age = d?.generated_at ? (Date.now() - Date.parse(d.generated_at)) / 86400000 : NaN;
  if (days && age > days) out.push(`<span class="statuspill warn" title="생성 후 ${Math.floor(age)}일 경과 (기준 ${days}일)">STALE</span>`);
  return out.join(' ');
}
const withFlags = (name, html) => { const f = flags(name); return f ? `<span>${f} ${html}</span>` : html; };
function formatErrorCard(title, e) {
  return `<div class="card" role="alert"><div class="row sectionhead"><h2>${esc(title)}</h2>${pill(FORMAT_ERR, 'bad')}</div><div class="sub">JSON 형식이 예상과 달라 이 패널을 표시할 수 없습니다 (${esc(e?.message || e)}). 다른 패널은 계속 표시됩니다.</div></div>`;
}
function genStamp(obj) { return obj?.generated_at ? `생성 ${esc(stamp(obj.generated_at))}` : ''; }
function intelHeading(title, desc, withHorizon) {
  const m = currentReal();
  return `<div class="heading"><div><h1>${title}</h1><div class="sub">${desc} · REAL DATA</div><div class="sub">${esc(markets[state.market].name)} 기준 거래일 ${esc(m?.as_of || '이용 불가')} ${m?.timezone ? '(' + esc(m.timezone) + ')' : ''} · 마지막 정상 수집 ${esc(stamp(m?.updated_at))}</div></div><div class="controls">${selector()}${withHorizon ? segments() : ''}</div></div>`;
}
const reg = (k = state.market) => intel.regime?.markets?.[k];
const fc = (k = state.market, h = horizon()) => intel.forecast?.markets?.[k]?.[String(h)];
const perf = (k = state.market, h = horizon()) => intel.performance?.markets?.[k]?.[String(h)];

/* ---------- charts ---------- */
const RANGES = {'1M': 21, '3M': 63, '6M': 126, '1Y': 252, 'ALL': 1e9};
function rangeButtons() {
  return `<div class="ranges" aria-label="차트 기간">${Object.keys(RANGES).map(r => `<button data-range="${r}" class="${state.range === r ? 'active' : ''}" aria-pressed="${state.range === r}">${r}</button>`).join('')}</div>`;
}
function chartRows() { return (currentReal()?.history || []).slice(-RANGES[state.range]); }
function regimeStrip(dates) {
  const hist = reg()?.history || [];
  const byDate = Object.fromEntries(hist.map(h => [h.date, h.regime]));
  const cells = dates.map(d => byDate[d]);
  if (!cells.some(Boolean)) return '<div class="tiny muted" style="margin-top:8px">국면 이력 없음 (regime 실행 필요 또는 이력 부족)</div>';
  return `<div class="strip" role="img" aria-label="시장 국면 이력">${cells.map((c, i) => `<i title="${esc(dates[i])} · ${esc(REG_KO[c] || '이력 부족')}" style="background:${REG_COLOR[c] || '#222a37'}"></i>`).join('')}</div><div class="legend">${Object.entries(REG_KO).map(([k, v]) => `<span><i style="background:${REG_COLOR[k]}"></i>${v}</span>`).join('')}</div>`;
}
function intelChart() {
  const m = currentReal();
  if (!hasPrices(m)) return unavailable(`${esc(markets[state.market].name)} 가격 추이`, esc(m?.error || '수집된 시세가 없습니다. 먼저 수집 명령을 실행하세요.'));
  const rows = chartRows(), values = rows.map(r => r.close), pts = coords(values, 800, 230);
  const first = values[0], last = values[values.length - 1];
  return `<div class="card"><div class="row"><div><h2>${esc(m.name)} 가격 추이</h2><div class="sub">${rows.length}관측일 · 일별 종가 · 보유 ${m.history.length}개 · 구간 수익률 ${sgn((last / first - 1) * 100, 2, '%')}</div></div>${rangeButtons()}</div><div class="row" style="margin-top:18px"><span class="index" style="margin:0">${number(m.value)}</span><span class="${direction(m.change_pct)}">${signed(m.change_pct)}</span></div><div class="sub">구간 최고 ${number(Math.max(...values))} / 최저 ${number(Math.min(...values))}</div><div class="chartwrap"><div class="tooltip" role="status"></div><svg class="pricechart" viewBox="0 0 800 250" preserveAspectRatio="none" role="group" aria-label="${esc(m.name)} 실제 일별 종가 차트">${[30, 90, 150, 210].map(y => `<line x1="0" y1="${y}" x2="800" y2="${y}" stroke="#253040" stroke-dasharray="3 5"/>`).join('')}<polyline points="${pts.map(p => p.join(',')).join(' ')}" fill="none" stroke="${last >= first ? '#55d9ad' : '#f18891'}" stroke-width="2" vector-effect="non-scaling-stroke"/>${rows.length <= 160 ? pts.map((p, i) => `<circle cx="${p[0]}" cy="${p[1]}" r="6" fill="transparent" tabindex="0" data-real-point="${i}" aria-label="${rows[i].date} 종가 ${number(rows[i].close)}"/>`).join('') : ''}</svg><div class="axis">${[0, Math.floor(rows.length / 3), Math.floor(rows.length * 2 / 3), rows.length - 1].map(i => `<span>${rows[i].date}</span>`).join('')}</div></div>${regimeStrip(rows.map(r => r.date))}${sourceNote(m)}</div>`;
}
bindRealChart = function () {
  const chart = document.querySelector('.pricechart'), tip = document.querySelector('.tooltip');
  if (!chart || !tip || !hasPrices(currentReal())) return;
  const rows = chartRows();
  const show = i => { i = Math.max(0, Math.min(rows.length - 1, i)); tip.textContent = `${rows[i].date} · 종가 ${number(rows[i].close)} · REAL DATA`; tip.style.display = 'block'; tip.style.left = `${Math.min(55, i / Math.max(rows.length - 1, 1) * 65)}%`; };
  const at = e => { const r = chart.getBoundingClientRect(); show(Math.round((e.clientX - r.left) / r.width * (rows.length - 1))); };
  chart.addEventListener('pointermove', at); chart.addEventListener('pointerdown', at);
  chart.addEventListener('pointerleave', () => { tip.style.display = 'none'; });
  document.querySelectorAll('[data-real-point]').forEach(el => {
    el.addEventListener('focus', () => show(+el.dataset.realPoint));
    el.addEventListener('blur', () => { tip.style.display = 'none'; });
  });
};
function miniChart(series, key, label, color = '#8baeff', extra = '') {
  const rows = (series || []).filter(r => Number.isFinite(r[key]));
  if (rows.length < 2) return `<div class="sub">${esc(label)} 이력 없음</div>`;
  const pts = coords(rows.map(r => r[key]), 400, 120);
  return `<svg class="minichart" viewBox="0 0 400 120" preserveAspectRatio="none" role="img" aria-label="${esc(label)} ${esc(rows[0].date)}–${esc(rows[rows.length - 1].date)}"><polyline points="${pts.map(p => p.join(',')).join(' ')}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"/>${extra}</svg><div class="axis"><span>${esc(rows[0].date)}</span><span>${esc(label)} ${num(rows[rows.length - 1][key])}</span><span>${esc(rows[rows.length - 1].date)}</span></div>`;
}

/* ---------- regime / risk ---------- */
const RISK_KO = {realized_vol: '변동성', drawdown: '낙폭', vix: 'VIX', credit: '신용', rates: '금리', krw: '원화'};
function regimeCard() {
  const r = reg();
  if (!intel.regime) return unavailable('Market Regime', missing('regime', '국면 분석'));
  if (!r || r.status !== 'ok') return unavailable('Market Regime', `${esc(markets[state.market].name)}: ${esc(r?.reason || r?.data_error || '분석 불가')}`);
  const g = r.risk;
  return `<div class="card"><div class="row sectionhead"><h2>Market Regime &amp; Risk</h2>${withFlags('regime', pill('RULE-BASED', ''))}</div><div class="regime ${regTone(r.regime)}">${esc(REG_KO[r.regime] || r.regime)}</div><div class="sub">${esc(r.regime)} · ${esc(r.regime_since)}부터 ${r.regime_days}거래일 · 추세 점수 ${sgn(r.trend_score, 0)}/5${r.raw_regime !== r.regime ? `<br><span class="neutral">당일 원시 신호: ${esc(REG_KO[r.raw_regime] || r.raw_regime)} (${r.confirm_days}일 연속 시 전환)</span>` : ''}</div>
  ${[['단기 추세 (20일선·5일)', r.short_term], ['중기 추세 (50/200일선·60일)', r.medium_term]].map(([a, b]) => `<div class="row riskrow"><span class="sub">${a}</span><span class="badge ${b === '상승' ? '' : b === '하락' ? 'negative' : 'neutral'}">${esc(b)}</span></div>`).join('')}
  <div class="row riskrow"><span class="sub">위험 수준</span><strong class="${g.score >= 50 ? 'negative' : g.score >= 25 ? 'neutral' : 'positive'}">${esc(g.level_ko)} · ${num(g.score, 1)}/100</strong></div>${g.components_available ? `<div class="tiny muted">구성요소 ${esc(g.components_available)}${Array.isArray(g.components_missing) && g.components_missing.length ? ` · 미수집 ${g.components_missing.map(c => esc(RISK_KO[c] || c)).join(', ')}` : ''}</div>` : ''}<div class="gauge" aria-hidden="true"><b style="left:calc(${Math.min(100, g.score || 0)}% - 1px)"></b></div>
  <div class="tiny muted">${Object.entries(g.components || {}).map(([k, v]) => `${esc(RISK_KO[k] || k)} ${v === null ? '미수집' : Math.round(v * 100)}`).join(' · ')}</div>
  <div class="note">출처 ${esc(r.source_id || '—')} · 기준 거래일 ${esc(r.as_of || '—')}<br>${(r.trend_rules || []).map(t => `${t.pass ? '✓' : '✕'} ${esc(t.label)}`).join(' &nbsp; ')}<br>국면은 현재 상태의 규칙 기반 서술이며 미래 수익률 예측이 아닙니다. ${genStamp(intel.regime)}</div></div>`;
}
function factorCards() {
  const r = reg();
  if (!r || r.status !== 'ok') return '';
  const list = (items, empty) => items.length ? items.map(t => `<div class="factor">${esc(t)}</div>`).join('') : `<div class="sub">${empty}</div>`;
  return `<div class="grid drivers"><div class="card"><h3 class="positive">↗ Bullish Factors <span class="muted tiny">/ 현재 수치 기반</span></h3>${list(r.bullish_factors, '해당 없음')}</div><div class="card bear"><h3 class="negative">↘ Bearish Factors / Risks <span class="muted tiny">/ 현재 수치 기반</span></h3>${list(r.bearish_factors, '해당 없음')}</div></div>`;
}
function macroPanel() {
  const s = intel.macro?.series;
  if (!s) return unavailable('Market Risk Overview', missing('macro', '거시 지표'));
  const rows = [['UST10Y', 2, '%'], ['UST2Y', 2, '%'], ['CURVE_2S10S', 2, '%p'], ['USDKRW', 1, ''], ['USD_BROAD', 2, ''], ['VIX', 1, ''], ['HY_OAS', 2, '%p']];
  return `<div class="card"><div class="row sectionhead"><h2>Market Risk Overview</h2>${withFlags('macro', '<span class="label">GLOBAL</span>')}</div>${rows.map(([k, d, u]) => {
    const v = s[k];
    if (!v) return `<div class="riskrow row"><span class="sub">${{VIX: 'VIX', HY_OAS: '하이일드 스프레드'}[k] || k}</span><span class="tiny muted">${k === 'VIX' || k === 'HY_OAS' ? '기본 비활성 (FRED 약관 · 옵트인)' : '미수집'}</span></div>`;
    return `<div class="riskrow row"><span class="sub">${esc(v.name)}<br><span class="tiny">${esc(v.as_of)} · ${esc(v.source_id)}</span></span><span class="riskvalue">${num(v.value, d, u)} ${Number.isFinite(v.chg20) ? `<span class="tiny ${v.chg20 > 0 ? 'negative' : 'positive'}">20일 ${sgn(k === 'USDKRW' || k === 'USD_BROAD' ? v.chg20 / (v.value - v.chg20) * 100 : v.chg20 * 100, k === 'USDKRW' || k === 'USD_BROAD' ? 2 : 0, k === 'USDKRW' || k === 'USD_BROAD' ? '%' : 'bp')}</span>` : ''}</span></div>`;
  }).join('')}<div class="note">출처: Federal Reserve Board H.15 / H.10 (공공 도메인). H.10 환율은 주 1회 게시되어 최대 약 1주 지연됩니다. ${genStamp(intel.macro)}</div></div>`;
}

/* ---------- forecast ---------- */
function probBars(f) {
  return [['상승 · Bullish', f.bullish_probability, 'positive', 'var(--green)'], ['횡보 · Neutral', f.neutral_probability, 'neutral', 'var(--amber)'], ['하락 · Bearish', f.bearish_probability, 'negative', 'var(--red)']]
    .map(([l, p, c, col]) => `<div class="prob-row"><div class="row"><span class="sub">${l}</span><span class="probvalue ${c}">${(p * 100).toFixed(0)}<small style="font-size:12px">%</small></span></div><div class="track"><i style="width:${p * 100}%;background:${col}"></i></div></div>`).join('');
}
function forecastCard() {
  const f = fc(), name = esc(markets[state.market].name);
  if (!intel.forecast) return unavailable('Market Forecast', missing('forecast', '예측'));
  if (!f || f.status === 'unavailable') {
    const g = intel.performance?.gate?.[state.market];
    return `<div class="card"><div class="row sectionhead"><h2>Market Forecast</h2>${withFlags('forecast', pill('UNAVAILABLE', 'bad'))}</div><div class="sub">${name} · ${horizon()}거래일 방향 확률을 제공하지 않습니다.</div><ul class="reasons"><li>${esc(f?.reason || '예측 기록 없음')}</li>${g && !g.allowed ? '<li>허용되지 않은 데이터로는 모델을 학습하지 않습니다. ML 학습 권한이 확인된 데이터를 <code>data/licensed/</code>에 등록하면 학습·검증이 활성화됩니다 (README 참고).</li>' : ''}</ul><div class="note">상승·횡보·하락 확률은 라이선스 확인 + 시간순 검증을 통과한 정량 모델에서만 표시됩니다. 임의·샘플 확률로 대체하지 않습니다.</div></div>`;
  }
  const validated = f.status === 'validated';
  return `<div class="card"><div class="row sectionhead"><h2>Market Forecast</h2>${withFlags('forecast', pill(validated ? 'VALIDATED' : 'NOT VALIDATED', validated ? 'ok' : 'warn'))}</div><div class="sub">${name} · ${f.horizon}거래일 · 기준 종가 ${esc(f.input_cutoff)} · 판정 예정 ${esc(f.expected_outcome_date)} (추정)</div>${validated ? '' : `<div class="note neutral" style="margin-top:12px;border:0;padding:0">⚠ 검증 기준 미충족 · 신뢰할 수 있는 예측이 아닙니다.<ul class="reasons">${(f.validation_reasons || []).map(r => `<li>${esc(r)}</li>`).join('')}</ul></div>`}<div style="${validated ? '' : 'opacity:.55'}">${probBars(f)}</div><div class="note">모델 ${esc(MODEL_KO[f.model_name] || f.model_name)} · ${esc(f.model_version)}<br>횡보 범위 ±${(f.threshold * 100).toFixed(2)}% (0.43·σ60·√h, 사전 고정) · 대상 데이터 ${esc(f.provenance?.target_source)} · ${genStamp(intel.forecast)}</div></div>`;
}
function overviewForecastCell(k) {
  const f = intel.forecast?.markets?.[k]?.['5'];
  if (!f || f.status === 'unavailable') return '<span class="tiny muted">5D 전망 이용 불가</span>';
  if (f.status !== 'validated') return '<span class="tiny muted">5D <strong class="neutral">신뢰할 수 없음</strong> · 미검증</span>';
  return `<span class="tiny muted">5D 상승 <strong class="positive">${pct(f.bullish_probability, 0)}</strong></span>`;
}
function intelCards() {
  return `<div class="grid markets">${Object.entries(markets).map(([key, demo]) => {
    const m = realData?.markets[key], r = reg(key);
    return `<button class="card market ${key === state.market ? 'active' : ''}" data-market="${key}" aria-pressed="${key === state.market}"><div class="row"><h2>${demo.name}</h2><span class="label">${esc(m?.symbol || '—')}</span></div><div class="index">${number(m?.value)}</div><div class="tiny ${direction(m?.change_pct)}" style="margin-top:6px">${Number.isFinite(m?.change_pct) ? (m.change_pct >= 0 ? '▲ ' : '▼ ') : ''}${signed(m?.change_pct)} <span class="muted">전 관측 종가 대비</span></div>${realSpark(m)}<div class="row"><span class="tiny muted">${esc(m?.as_of || '기준일 없음')}</span>${r?.status === 'ok' ? `<span class="badge ${regTone(r.regime)}">${esc(REG_KO[r.regime])}</span>` : '<span class="badge neutral">국면 이용 불가</span>'}</div><div class="row" style="margin-top:10px">${overviewForecastCell(key)}${r?.status === 'ok' ? `<span class="tiny muted">위험 ${esc(r.risk?.level_ko || '—')}</span>` : ''}</div><div class="sub" style="margin-top:6px">${esc(availability(m))}</div></button>`;
  }).join('')}</div>`;
}
function liveHistoryTable(rows, title = 'Forecast History · 실시간 예측 기록') {
  const list = (rows || []).filter(r => r.market === state.market && r.horizon === horizon()).slice(0, 30);
  return `<div class="card" style="margin-top:18px"><div class="row sectionhead"><h2>${title}</h2>${pill('LIVE · 추가 전용', '')}</div>${list.length ? `<div class="tablewrap"><table><thead><tr><th>기준 종가일</th><th>모델 버전</th><th>상승</th><th>횡보</th><th>하락</th><th>검증</th><th>판정일</th><th>실제 수익률</th><th>결과</th></tr></thead><tbody>${list.map(r => `<tr><td>${esc(r.input_cutoff)}</td><td class="tiny">${esc(r.model_version)}</td><td>${pct(r.p_bull, 0)}</td><td>${pct(r.p_neutral, 0)}</td><td>${pct(r.p_bear, 0)}</td><td>${pill(r.validation_status, statusKind(r.validation_status))}</td><td>${esc(r.outcome_date || r.expected_outcome_date + ' (예정)')}</td><td>${Number.isFinite(r.actual_return) ? sgn(r.actual_return * 100, 2, '%') : '—'}</td><td class="${r.hit === null ? 'muted' : r.hit ? 'positive' : 'negative'}">${r.status === 'pending' ? 'Pending' : r.hit ? '✓ ' + esc(r.actual_class) : '✕ ' + esc(r.actual_class)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="sub">이 시장·기간의 실시간 예측 기록이 없습니다. 예측은 생성 시점에 저장되며 이후 덮어쓰지 않습니다.</div>'}<div class="note">백테스트 결과는 Model Performance에서 별도 표시합니다. 판정은 h번째 다음 관측 종가와 예측 당시 저장한 횡보 임계값으로 계산합니다.</div></div>`;
}
function featurePanel() {
  const f = fc(), p = perf();
  if (!f || f.status === 'unavailable' || !p) return '';
  const imp = p.lightgbm_importance || [];
  return `<div class="card"><div class="row sectionhead"><h2>Forecast Drivers</h2><span class="label">${horizon()}D</span></div><div class="sub">LightGBM 정보이득 비중 (개발 구간 적합 · 설명용) 및 현재 입력값</div>${imp.slice(0, 8).map(r => `<div class="row riskrow"><span class="sub">${esc(r.feature)}</span><span><span class="tiny muted">현재 ${num(f.features?.[r.feature])}</span> &nbsp;<strong>${pct(r.gain_share)}</strong></span></div>`).join('') || '<div class="sub">중요도 정보 없음</div>'}<div class="note">변수 중요도는 인과 관계나 방향을 의미하지 않습니다. 제외된 입력: ${(p.dropped_features || []).map(d => esc(d.feature)).join(', ') || '없음'}</div></div>`;
}

/* ---------- briefing ---------- */
function briefingCard(full) {
  const b = intel.briefing;
  if (!b) return `<div class="card brief">${missing('briefing', '브리핑')}</div>`;
  const s = b.sections, li = a => `<ul class="brieflist">${(Array.isArray(a) ? a : []).map(x => `<li>${esc(x)}</li>`).join('')}</ul>`;
  const parts = [['1 · Executive Summary', `<p>${esc(s.executive_summary)}</p>`], ['2 · US Market Overview', li(s.us_overview)], ['3 · Korea Market Overview', li(s.korea_overview)],
    ['4 · Bullish Factors', li(s.bullish_factors)], ['5 · Bearish Factors', li(s.bearish_factors)], ['6 · Market Forecast (정량 모델)', li(s.market_forecast)],
    ['7 · Outlook Changes', li(s.outlook_changes)],
    ['8 · Events to Watch', s.events_to_watch?.length ? `<ul class="brieflist">${s.events_to_watch.map(e => `<li>${esc(e.date)} · ${e.url ? link(e.url, e.title) : esc(e.title)} <span class="tiny muted">(${esc(e.source)})</span></li>`).join('')}</ul>` : `<p class="sub">향후 45일 내 확인된 일정 없음${b.events_status?.fomc === 'failed' ? ' · FOMC 일정 조회 실패' : ''}</p>`]];
  const news = s.news?.length ? `<h3 style="margin-top:18px">관련 뉴스 (중요도 3 이상)</h3><ul class="brieflist">${s.news.map(n => `<li>${link(n.url, n.title)} <span class="tiny muted">${esc(n.source)} · ${esc(n.category)}</span></li>`).join('')}</ul>` : '';
  return `<div class="card brief"><div class="row"><h2>✧ Daily Market Brief</h2>${withFlags('briefing', pill(String(b.method || '').includes('llm') ? 'AI 요약 + 규칙' : 'RULE-BASED', ''))}</div><div class="sub">${esc(b.session === 'morning' ? '아침' : '저녁')} 브리핑 · ${genStamp(b)} · LLM ${esc(b.llm_status)}</div>${Array.isArray(b.input_warnings) && b.input_warnings.length ? `<div class="note neutral" role="alert">⚠ 입력 데이터 경고<ul class="reasons">${b.input_warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>` : ''}${(full ? parts : parts.slice(0, 1).concat(parts.slice(5, 7))).map(([h, body]) => `<h3 style="margin:18px 0 4px">${h}</h3>${body}`).join('')}${full ? news : ''}<div class="note">사용 데이터: ${(b.sources || []).map(x => esc(x.label + ' (' + x.detail + ')')).join(' · ')}<br>${esc(b.disclaimer)}</div></div>`;
}

/* ---------- performance ---------- */
function perfView() {
  const p = perf(), name = esc(markets[state.market].name);
  let body = '';
  if (!intel.performance) body = unavailable('Model Performance', missing('performance', '모델 성능'));
  else if (!p || p.status !== 'ok') body = `<div class="card"><div class="row sectionhead"><h2>Model Performance</h2>${withFlags('performance', pill('UNAVAILABLE', 'bad'))}</div><div class="sub">${name} · ${horizon()}D: 실제 모델 학습·검증 결과가 없습니다.</div><ul class="reasons"><li>${esc(p?.reason || '학습 미실행')}</li></ul><div class="note">성능 수치를 임의로 표시하지 않습니다. 검증 파이프라인(시간순 walk-forward · purge · 개발/holdout 분리 · 확률 보정)은 구현되어 있고 합성 데이터로 테스트에서만 확인했습니다.</div></div>`;
  else body = perfDetail(p);
  return intelHeading('Model Performance', '시간순 walk-forward 백테스트 · 실시간 예측 성과', true) + body + liveSummaryCard() + liveHistoryTable(intel.forecast?.history) + licenseTable();
}
// strongest baseline comparison (optional key; shown next to vs_class_frequency when present)
function bestBase(b) {
  if (!isObj(b) || !b.baseline) return '';
  return `<br><span class="tiny muted">최강 기준 ${esc(MODEL_KO[b.baseline] || b.baseline)} 대비 LL ${sgn(b.log_loss_improvement, 3)} [${num(b.ci90_low, 3)}, ${num(b.ci90_high, 3)}] · Brier ${sgn(b.brier_improvement, 3)} (하한 ${num(b.brier_ci90_low, 3)})</span>`;
}
function perfDetail(p) {
  const models = p.models, names = Object.keys(models);
  if (!state.perfModel || !models[state.perfModel]) state.perfModel = p.primary_model;
  const m = models[state.perfModel], base = models.class_frequency;
  const warn = [];
  Object.entries(models).forEach(([n, r]) => { if (r.effective_n < 50) warn.push(`${MODEL_KO[n] || n}: 비중첩 표본 ${r.effective_n}개로 적음`); if (r.ece > 0.08) warn.push(`${MODEL_KO[n] || n}: 확률 보정 오차 ECE ${r.ece.toFixed(3)}`); });
  const cmLabels = ['하락', '횡보', '상승'];
  const cm = m.confusion_matrix, cmMax = Math.max(...cm.flat(), 1);
  const rel = (m.reliability?.bullish || []);
  const calSvg = `<svg viewBox="0 0 360 240" style="width:100%;height:220px" role="img" aria-label="상승 확률 보정 차트"><path d="M40 20V200H330" stroke="#465268" fill="none"/><path d="M40 200L330 20" stroke="#56627a" stroke-dasharray="5 5"/>${[0, 25, 50, 75, 100].map((n, i) => `<text x="30" y="${204 - i * 45}" fill="#8995a8" text-anchor="end" font-size="10">${n}</text><text x="${40 + i * 72.5}" y="218" text-anchor="middle" fill="#8995a8" font-size="10">${n}%</text>`).join('')}${rel.map(b => `<circle cx="${40 + b.predicted * 290}" cy="${200 - b.observed * 180}" r="${Math.min(9, 2 + Math.sqrt(b.n) / 3)}" fill="#8baeff" opacity=".8"><title>예측 ${(b.predicted * 100).toFixed(1)}% · 관측 ${(b.observed * 100).toFixed(1)}% · n=${b.n}</title></circle>`).join('')}<polyline points="${rel.map(b => `${40 + b.predicted * 290},${200 - b.observed * 180}`).join(' ')}" fill="none" stroke="#8baeff" stroke-width="1.5"/><text x="180" y="238" text-anchor="middle" fill="#8995a8" font-size="10">예측 상승 확률 (holdout)</text></svg>`;
  return `<div class="sub" style="margin-bottom:12px">BACKTEST · 개발 구간 ${esc(p.spans.development.join(' ~ '))} (튜닝·보정 방법 선택) → holdout ${esc(p.spans.holdout.join(' ~ '))} (보고). 주 모델: ${esc(MODEL_KO[p.primary_model] || p.primary_model)} (${esc(p.primary_rule)}) · run ${esc(p.run_id)}</div>
  ${warn.length ? `<div class="card" role="alert" style="border-color:#695735;margin-bottom:18px"><strong class="neutral">⚠ 검증 경고</strong><ul class="reasons">${warn.map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>` : ''}
  <div class="grid kpis">${[['Accuracy', pct(m.accuracy), `기준 ${pct(base.accuracy)}`], ['Balanced Accuracy', pct(m.balanced_accuracy), `기준 ${pct(base.balanced_accuracy)}`], ['Brier Score ↓', num(m.brier, 3), `기준 ${num(base.brier, 3)} · skill ${sgn(m.vs_class_frequency.brier_skill * 100, 1, '%')}`], ['Log Loss ↓', num(m.log_loss, 3), `기준 ${num(base.log_loss, 3)} · 개선 90%CI [${num(m.vs_class_frequency.ci90_low, 3)}, ${num(m.vs_class_frequency.ci90_high, 3)}]${bestBase(m.vs_best_baseline)}`]].map(([a, b, c]) => `<div class="card"><div class="label">${a}</div><div class="kpi">${b}</div><div class="sub">${c}</div><div class="tiny muted" style="margin-top:8px">${esc(MODEL_KO[state.perfModel])} · n=${m.n}</div></div>`).join('')}</div>
  <div class="card"><div class="row sectionhead"><h2>Model Comparison</h2>${withFlags('performance', pill('HOLDOUT · 동일 fold', ''))}</div><div class="tablewrap"><table><thead><tr><th>모델</th><th>Accuracy</th><th>Balanced</th><th>Macro F1</th><th>Brier ↓</th><th>Log Loss ↓</th><th>ECE ↓</th><th>LL 개선 90%CI</th><th>보정</th><th>판정</th></tr></thead><tbody>${names.map(n => { const r = models[n], v = r.vs_class_frequency; return `<tr><td><button class="filter ${n === state.perfModel ? 'active' : ''}" data-perf-model="${esc(n)}" style="padding:5px 8px">${esc(MODEL_KO[n] || n)}</button></td><td>${pct(r.accuracy)}</td><td>${pct(r.balanced_accuracy)}</td><td>${num(r.macro_f1, 3)}</td><td>${num(r.brier, 3)}</td><td>${num(r.log_loss, 3)}</td><td>${num(r.ece, 3)}</td><td>${n === 'class_frequency' ? '기준' : `[${num(v.ci90_low, 3)}, ${num(v.ci90_high, 3)}]${bestBase(r.vs_best_baseline)}`}</td><td>${esc(r.calibration)}</td><td>${pill(r.validation.status, statusKind(r.validation.status))}</td></tr>`; }).join('')}</tbody></table></div><div class="note">판정 기준(사전 고정): 비중첩 holdout 표본 ≥ 50, 기준 모델 대비 log loss 개선 블록 부트스트랩 90% 하한 > 0, Brier skill > 0, 균형 정확도 기준 대비 +2%p 이상, ECE ≤ 0.08. ${m.validation.reasons.length && state.perfModel !== 'class_frequency' ? '<br>' + esc(MODEL_KO[state.perfModel]) + ': ' + m.validation.reasons.map(esc).join(' · ') : ''}</div></div>
  <div class="grid two" style="margin-top:18px"><div class="card"><div class="row"><h2>Calibration Chart</h2>${pill('상승 클래스', '')}</div><div class="sub" style="margin-top:8px">점선: 완전 보정 · 점 크기: 표본 수 · ECE ${num(m.ece, 3)}</div>${calSvg}</div><div class="card"><div class="row sectionhead"><h2>Confusion Matrix</h2><span class="label">행: 실제 / 열: 예측</span></div><table class="cm"><thead><tr><th></th>${cmLabels.map(l => `<th>${l}</th>`).join('')}</tr></thead><tbody>${cm.map((row, i) => `<tr><th>${cmLabels[i]}</th>${row.map((v, j) => `<td class="h" style="background:rgba(${i === j ? '85,217,173' : '241,136,145'},${(v / cmMax * .45).toFixed(3)})">${v}</td>`).join('')}</tr>`).join('')}</tbody></table><div class="note">실제 클래스 비율: 하락 ${pct(m.class_share[0])} · 횡보 ${pct(m.class_share[1])} · 상승 ${pct(m.class_share[2])}</div></div></div>
  <div class="card" style="margin-top:18px"><div class="row"><h2>Rolling Model Performance</h2><span class="label">63관측일 정확도</span></div>${miniChart(m.rolling_accuracy, 'accuracy', '정확도', '#8baeff')}<div class="note">비교선 없음 · 기준 모델 정확도 ${pct(base.accuracy)} · 시기별 성능 변동을 확인하세요.</div></div>`;
}
function liveSummaryCard() {
  const s = (intel.forecast?.live_summary || []).filter(r => r.market === state.market);
  if (!s.length) return '';
  return `<div class="card" style="margin-top:18px"><h2>실시간 예측 성과 (판정 완료분)</h2><div class="tablewrap"><table><thead><tr><th>기간</th><th>판정 수</th><th>적중률</th><th>Brier</th><th>균등확률 대비</th><th>비고</th></tr></thead><tbody>${s.map(r => `<tr><td>${esc(r.horizon)}D</td><td>${esc(r.n)}</td><td>${pct(r.accuracy)}</td><td>${num(r.brier, 3)}</td><td>${Number.isFinite(r.skill_vs_uniform) ? `skill ${sgn(r.skill_vs_uniform * 100, 1, '%')} <span class="tiny muted">(기준 ${num(r.brier_uniform, 3)})</span>` : '—'}</td><td class="wrap tiny ${r.warning ? 'neutral' : 'muted'}">${r.warning ? '⚠ ' + esc(r.warning) : ''}</td></tr>`).join('')}</tbody></table></div>${s.some(r => r.n < 30) ? '<div class="note neutral">판정 표본이 30개 미만이라 통계적 의미가 제한적입니다.</div>' : ''}</div>`;
}
function licenseTable() {
  const L = intel.performance?.licenses || intel.status?.licenses;
  if (!L) return '';
  const k = v => ({allowed: 'ok', conditional: 'warn', user_supplied: 'warn', not_allowed: 'bad', unclear: 'warn', unverified: 'warn'}[v] || '');
  return `<div class="card" style="margin-top:18px"><div class="row sectionhead"><h2>Data Sources &amp; Licences</h2><span class="label">확인 ${esc(L[0]?.verified_on || '')}</span></div><div class="tablewrap"><table><thead><tr><th>Provider / Dataset</th><th>수집</th><th>로컬 저장</th><th>ML 학습</th><th>재배포</th><th>비고</th></tr></thead><tbody>${L.map(r => `<tr><td class="wrap">${link(r.terms_url, r.provider)}<br><span class="tiny muted">${esc(r.dataset)} · ${esc(r.id)}</span></td>${['collection', 'local_storage', 'ml_training', 'redistribution'].map(f => `<td>${pill(r[f] || '—', k(r[f]))}</td>`).join('')}<td class="wrap tiny muted">${esc(r.notes || '')}</td></tr>`).join('')}</tbody></table></div><div class="note">확인되지 않은 권한은 허용으로 간주하지 않습니다. ML 학습은 'allowed'인 소스만 사용합니다. 법률 자문이 아닙니다.</div></div>`;
}

/* ---------- indicators ---------- */
function indicatorsView() {
  const r = reg(), s = intel.macro?.series || {};
  const volChart = r?.status === 'ok' ? `<div class="card"><div class="row"><h2>변동성 추이</h2><span class="label">20일 / 60일 연율화</span></div>${miniChart(r.vol_history, 'vol20', '20일 실현변동성 %', '#f18891')}${miniChart(r.vol_history, 'vol60', '60일 실현변동성 %', '#e6c17b')}<div class="note">현재 20일 변동성은 최근 1년 분포의 ${pct(r.metrics.vol20_pct, 0)} 백분위 · 52주 고점 대비 ${sgn(r.metrics.drawdown252, 1, '%')}</div></div>` : unavailable('변동성 추이', esc(r?.reason || '국면 분석 없음'));
  const macroCards = ['UST10Y', 'UST2Y', 'UST3M', 'USDKRW', 'USD_BROAD', 'VIX', 'HY_OAS', 'IG_OAS'].filter(k => s[k]).map(k => `<div class="card"><div class="row"><h2>${esc(s[k].name)}</h2><span class="label">${esc(s[k].source_id)}</span></div><div style="font-size:23px;margin-top:9px">${num(s[k].value, k.startsWith('UST') || k.endsWith('OAS') ? 2 : 1, s[k].unit === '%' ? '%' : s[k].unit === '%p' ? '%p' : '')}</div><div class="sub">${esc(s[k].as_of)} 기준 · 1년 백분위 ${pct(s[k].pct1y, 0)}</div>${miniChart(s[k].history, 'value', s[k].name)}</div>`).join('');
  const regHist = r?.status === 'ok' ? `<div class="card" style="margin-top:18px"><div class="row sectionhead"><h2>국면 변화 이력</h2><span class="label">최근 ${r.history.length}관측일</span></div>${regimeStrip(r.history.map(h => h.date))}<div class="tablewrap" style="margin-top:14px"><table><thead><tr><th>전환일</th><th>이전</th><th>이후</th></tr></thead><tbody>${r.transitions.slice().reverse().map(t => `<tr><td>${esc(t.date)}</td><td>${esc(REG_KO[t.from])}</td><td class="${regTone(t.to)}">${esc(REG_KO[t.to])}</td></tr>`).join('') || '<tr><td colspan="3">전환 없음</td></tr>'}</tbody></table></div><div class="note">각 날짜의 국면은 그날까지의 데이터로만 계산했습니다 (사후 스무딩 없음). ${r.confirm_days}일 연속 확인 규칙 적용. 분포: ${Object.entries(r.counts).map(([k, v]) => `${REG_KO[k]} ${v}`).join(' · ')}</div></div>` : '';
  return intelHeading('Market Indicators', '기술적·거시경제·위험 지표') + realIndicatorCards() + `<div class="grid two" style="margin-top:18px">${volChart}${macroPanel()}</div>` + (macroCards ? `<h2 style="margin:26px 0 14px">거시경제 지표 추이</h2><div class="grid indicators">${macroCards}</div>` : missing('macro', '거시 지표')) + regHist;
}

/* ---------- news ---------- */
function newsIntelView() {
  const n = intel.news;
  if (!n) return intelHeading('News Intelligence', '금융 뉴스·공식 발표 분석') + unavailable('News', missing('news', '뉴스'));
  const items = n.items.filter(i => (state.newsCat === 'All' || i.category === state.newsCat) && (state.newsMarket === 'all' || (i.related_markets || []).includes(state.newsMarket)));
  const cats = ['All', ...new Set(n.items.map(i => i.category))];
  const counts = ['positive', 'neutral', 'negative'].map(s => items.filter(i => i.sentiment_label === s).length), scored = counts.reduce((a, b) => a + b, 0);
  const rep = n.report || {};
  const sentLabel = {positive: '긍정', neutral: '중립', negative: '부정'};
  return intelHeading('News Intelligence', '공식 발표·뉴스 메타데이터 분석 (예측 모델과 분리)') + `<div class="card"><div class="row"><h2>News Sentiment</h2>${flags('news')}<span class="sub">${items.length}건 · 최근 14일 · ${genStamp(n)}</span></div>${scored ? `<div class="distribution">${counts.map((c, i) => `<span style="width:${c / scored * 100}%;background:${['var(--green)', 'var(--amber)', 'var(--red)'][i]}"></span>`).join('')}</div><div class="row"><span class="positive tiny">● 긍정 ${counts[0]}</span><span class="neutral tiny">● 중립 ${counts[1]}</span><span class="negative tiny">● 부정 ${counts[2]}</span></div>` : '<div class="sub" style="margin-top:12px">감성 점수 대상 없음</div>'}<div class="note">${esc(n.sentiment_note)} · ${esc(n.separation_note)}<br>수집원: ${['fed_press_rss', 'sec_press_rss', 'gdelt_doc'].map(k => `${k} ${pill(rep[k]?.status || '—', statusKind(rep[k]?.status))}${rep[k]?.error ? ' <span class="tiny neutral">' + esc(rep[k].error) + '</span>' : ''}`).join(' · ')}<br>기사 본문은 저장하지 않습니다 (제목·URL·시각만). 원문은 링크로 확인하세요.</div></div>
  <div class="filters" aria-label="뉴스 카테고리">${cats.map(c => `<button class="filter ${state.newsCat === c ? 'active' : ''}" data-news-cat="${esc(c)}" aria-pressed="${state.newsCat === c}">${c === 'All' ? '전체' : esc(c)}</button>`).join('')}</div><div class="filters" aria-label="관련 시장" style="margin-top:-10px">${[['all', '전체 시장'], ['sp', 'S&P 500'], ['nq', 'NASDAQ 100'], ['ks', 'KOSPI'], ['kq', 'KOSDAQ']].map(([k, l]) => `<button class="filter ${state.newsMarket === k ? 'active' : ''}" data-news-market="${k}" aria-pressed="${state.newsMarket === k}">${l}</button>`).join('')}</div>
  <div class="grid newsgrid">${items.slice(0, 60).map(i => `<article class="card news newsitem"><div class="row"><span class="label">${esc(i.category)}</span>${i.sentiment_label ? `<span class="badge ${tone(sentLabel[i.sentiment_label])}">${sentLabel[i.sentiment_label]}</span>` : '<span class="badge neutral">감성 미산출</span>'}</div><h3>${link(i.url, i.title)}</h3><div class="tiny muted">${esc(i.source)} · ${esc(stamp(i.published_at || i.collected_at))} · 중요도 ${i.importance}/5 · ${(i.related_markets || []).map(k => esc(markets[k]?.name || k)).join(', ')}</div><div class="tiny muted" style="margin-top:8px">${esc(i.provider)} · 감성 방법 ${esc(i.sentiment_method || '—')}</div></article>`).join('') || '<div class="card sub">조건에 맞는 뉴스가 없습니다.</div>'}</div>`;
}

/* ---------- system status ---------- */
function statusView() {
  const s = intel.status;
  if (!s) return intelHeading('System Status', '수집·모델·작업 상태', false) + unavailable('System Status', missing('status', '상태'));
  const pr = Object.entries(s.prices).map(([k, v]) => `<tr><td>${esc(markets[k]?.name || k)}</td><td>${pill(v.status || 'unavailable', statusKind(v.status || 'unavailable'))}</td><td>${esc(v.as_of || '—')}</td><td>${esc(stamp(v.updated_at))}</td><td>${esc(stamp(v.last_attempt_at))}</td><td>${esc((s.price_archive?.[k] || [])[0] || 0)}</td><td class="wrap tiny">${esc(v.error || '')}</td></tr>`).join('');
  const ingest = s.ingest?.macro || {};
  const mc = Object.entries(ingest).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${pill(v.status, statusKind(v.status))}</td><td>${esc(v.as_of || '—')}</td><td>${esc((s.macro?.[k] || {}).n || 0)}</td><td class="wrap tiny">${esc(v.error || '')}</td></tr>`).join('');
  const lj = s.last_jobs || {};
  const g = s.model?.gate || {};
  return intelHeading('System Status', '수집원·데이터 기준시각·모델·작업 이력', false) + `<div class="grid kpis">${[['마지막 수집', lj.ingest], ['마지막 학습', lj.train], ['마지막 예측', lj.forecast], ['마지막 브리핑', lj.briefing]].map(([a, j]) => `<div class="card"><div class="label">${a}</div><div style="font-size:15px;margin:12px 0 8px">${esc(j ? stamp(j.finished_at || j.started_at) : '실행 기록 없음')}</div>${j ? pill(j.status, statusKind(j.status)) : ''}<div class="sub" style="margin-top:8px">${esc(j?.message || '')}</div></div>`).join('')}</div>
  <div class="card"><div class="row sectionhead"><h2>가격 수집원</h2>${flags('status')}</div><div class="tablewrap"><table><thead><tr><th>시장</th><th>상태</th><th>기준일</th><th>정상 수집</th><th>마지막 시도</th><th>보관 행</th><th>오류</th></tr></thead><tbody>${pr}</tbody></table></div><div class="note">FRED 시세는 약관(저장·ML 금지) 때문에 SQLite에 보관하지 않고 v0.2 스냅샷만 사용합니다. KRX는 인증키 승인 후 수집됩니다.</div></div>
  <div class="card" style="margin-top:18px"><h2>거시 지표 수집원</h2><div class="tablewrap"><table><thead><tr><th>시리즈</th><th>상태</th><th>최근 관측</th><th>보관 행</th><th>비고</th></tr></thead><tbody>${mc || '<tr><td colspan="5">수집 기록 없음</td></tr>'}</tbody></table></div></div>
  <div class="card" style="margin-top:18px"><h2>모델 학습 상태</h2>${Object.entries(g).map(([k, v]) => `<div class="row riskrow"><span class="sub">${esc(markets[k]?.name || k)} · ${esc(v.source_id)} · ${v.observations}관측</span>${v.allowed ? pill('학습 허용', 'ok') : `<span>${pill('학습 차단', 'bad')} <span class="tiny muted">${esc(v.reason)}</span></span>`}</div>`).join('') || '<div class="sub">학습 기록 없음</div>'}<div class="note">학습 시각 ${esc(stamp(s.model.trained_at))} · 검증 완료 조합 ${s.model.trained_pairs}개 · 실시간 예측 기록 ${s.forecast.live_records}건 (대기 ${s.forecast.pending})</div></div>
  <div class="card" style="margin-top:18px"><h2>작업 이력</h2><div class="tablewrap"><table><thead><tr><th>작업</th><th>시작</th><th>종료</th><th>상태</th><th>메시지</th></tr></thead><tbody>${s.jobs.slice(0, 20).map(j => `<tr><td>${esc(j.job)}</td><td>${esc(stamp(j.started_at))}</td><td>${esc(stamp(j.finished_at))}</td><td>${pill(j.status, statusKind(j.status))}</td><td class="wrap tiny">${esc(j.message || '')}</td></tr>`).join('')}</tbody></table></div><div class="note">자동 실행은 로컬 컴퓨터가 켜져 있을 때만 동작합니다. 상태 파일 생성 ${esc(stamp(s.generated_at))} · 엔진 ${esc(s.engine_version)}</div></div>` + licenseTable();
}

/* ---------- views ---------- */
function intelOverview() {
  return intelHeading('Daily Market Outlook', '시장 국면·위험·전망을 한눈에', true) + intelCards() + `<div class="grid two">${intelChart()}${regimeCard()}</div><div class="grid drivers">${forecastCard()}${macroPanel()}</div>` + factorCards() + briefingCard(true);
}
function intelForecast() {
  return intelHeading('Market Forecast', '시장별 1D / 5D / 20D 방향 확률과 근거', true) + `<div class="grid two">${intelChart()}${forecastCard()}</div><div class="grid drivers">${featurePanel() || regimeCard()}${modelStatusCard()}</div>` + liveHistoryTable(intel.forecast?.history);
}
function modelStatusCard() {
  const p = perf();
  if (!p || p.status !== 'ok') return `<div class="card"><div class="row sectionhead"><h2>현재 모델 상태</h2>${pill('미학습', 'bad')}</div><div class="sub">${esc(p?.reason || '학습 결과 없음')}</div><div class="note">${genStamp(intel.performance)}</div></div>`;
  const m = p.models[p.primary_model];
  return `<div class="card"><div class="row sectionhead"><h2>현재 모델 상태</h2>${pill(m.validation.status, statusKind(m.validation.status))}</div>${[['주 모델', MODEL_KO[p.primary_model]], ['보정 방법', m.calibration], ['holdout 기간', p.spans.holdout.join(' ~ ')], ['holdout 정확도 / 기준', `${pct(m.accuracy)} / ${pct(p.models.class_frequency.accuracy)}`], ['Brier skill', sgn(m.vs_class_frequency.brier_skill * 100, 1, '%')], ['비중첩 표본', m.effective_n]].map(([a, b]) => `<div class="row riskrow"><span class="sub">${a}</span><strong>${esc(b)}</strong></div>`).join('')}<div class="note">세부 지표는 Model Performance 탭 · ${genStamp(intel.performance)}</div></div>`;
}
/* ---------- per-panel isolation: a malformed JSON breaks only its own panel ---------- */
const guard = (title, fn, heading) => function (...a) {
  try { return fn.apply(this, a); } catch (e) {
    console.warn(`intel panel "${title}" render failed:`, e);
    return (heading ? intelHeading(title, '형식 오류', false) : '') + formatErrorCard(title, e);
  }
};
intelCards = guard('Market Overview', intelCards); intelChart = guard('가격 추이', intelChart);
regimeCard = guard('Market Regime & Risk', regimeCard); factorCards = guard('Bullish / Bearish Factors', factorCards);
macroPanel = guard('Market Risk Overview', macroPanel); forecastCard = guard('Market Forecast', forecastCard);
featurePanel = guard('Forecast Drivers', featurePanel); modelStatusCard = guard('현재 모델 상태', modelStatusCard);
briefingCard = guard('Daily Market Brief', briefingCard); perfDetail = guard('Model Performance', perfDetail);
liveSummaryCard = guard('실시간 예측 성과', liveSummaryCard); liveHistoryTable = guard('Forecast History', liveHistoryTable);
licenseTable = guard('Data Sources & Licences', licenseTable);
newsIntelView = guard('News Intelligence', newsIntelView, true); statusView = guard('System Status', statusView, true);
indicatorsView = guard('Market Indicators', indicatorsView, true);
realView = function () {
  const notice = loading ? '<div class="note" role="status">로컬 JSON을 읽고 있습니다…</div>' : loadError ? `<div class="card" role="alert" style="margin-top:18px;border-color:#695735">${esc(loadError)}${realData ? ' · 마지막으로 읽은 정상 JSON을 유지합니다.' : ' · 샘플 데이터로 대체하지 않습니다.'}<div class="sub">로컬 서버 실행 후 <code>.venv/bin/python scripts/mi_cli.py ingest</code> → JSON 다시 읽기</div></div>` : '';
  const views = {Overview: intelOverview, Forecast: intelForecast, Indicators: indicatorsView, 'News Intelligence': newsIntelView, 'Model Performance': perfView, 'System Status': statusView};
  return notice + (views[state.tab] || intelOverview)();
};
document.addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b || state.mode !== 'real') return;
  if (b.dataset.range) { state.range = b.dataset.range; render(); }
  if (b.dataset.newsCat) { state.newsCat = b.dataset.newsCat; render(); }
  if (b.dataset.newsMarket) { state.newsMarket = b.dataset.newsMarket; render(); }
  if (b.dataset.perfModel) { state.perfModel = b.dataset.perfModel; render(); }
  if (b.dataset.market || b.dataset.horizon !== undefined) state.perfModel = null;
});
loadIntel().then(render);
