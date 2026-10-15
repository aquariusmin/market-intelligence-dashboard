/* Market Intelligence dashboard (REAL DATA mode). DEMO views and sample data stay in index.html.
   Reads local JSON only: data/markets.json (price snapshot), data/reference_only/macro.json (display-only macro),
   data/dashboard/*.json (analysis written by scripts/mi_cli.py). Nothing is computed from sample data. */
state.mode = 'real';
state.chartPeriod = '6M';
state.smaLines = new Set([20]);
let macroData = null;
let macroLoadError = '';
let realData = null;
let loadError = '';
let loading = false;
const sourceLinks = {
  sp: 'https://fred.stlouisfed.org/series/SP500',
  nq: 'https://fred.stlouisfed.org/series/NASDAQ100',
  ks: 'https://openapi.krx.co.kr/contents/OPP/MAIN/main/index.cmd',
  kq: 'https://openapi.krx.co.kr/contents/OPP/MAIN/main/index.cmd'
};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = (v, suffix='') => Number.isFinite(v) ? fmt(v) + suffix : '—';
const signed = v => Number.isFinite(v) ? `${v > 0 ? '+' : ''}${v.toFixed(2)}%` : '이용 불가';
const direction = v => !Number.isFinite(v) || v === 0 ? 'neutral' : v > 0 ? 'positive' : 'negative';
const stamp = value => value ? new Date(value).toLocaleString('ko-KR', {timeZone:'Asia/Seoul', hour12:false}) + ' KST' : '없음';
const currentReal = () => realData?.markets[state.market];
const hasPrices = m => !!m?.history?.length;
function availability(m) {
  if (!hasPrices(m)) return '이용 불가';
  if (m.provisional) return m.provisional.note || '최신 종가 잠정치';
  if (m.status === 'stale') return '수집 실패 · 이전 정상 데이터';
  const age = (Date.now()-Date.parse(m.as_of+'T00:00:00Z'))/86400000;
  return age > 4 ? '4일 이상 경과 · 휴장일 미반영' : '일별 종가 · 실시간 아님';
}
function renderModeChrome() {
  document.querySelector('.topbar').innerHTML = `<span>Market Intelligence <span class="muted">/ local</span></span><div class="controls"><div class="segments" aria-label="데이터 모드"><button data-mode="real" aria-pressed="${state.mode==='real'}" class="${state.mode==='real'?'active':''}">REAL DATA</button><button data-mode="demo" aria-pressed="${state.mode==='demo'}" class="${state.mode==='demo'?'active':''}">DEMO</button></div><button class="filter" data-reload ${loading?'disabled':''}>${loading?'로딩 중…':'JSON 다시 읽기'}</button></div>`;
  document.querySelector('.sidefoot').innerHTML = `<span class="statusdot"></span>Local<br>${state.mode==='real'?'일별 종가 · 실시간 아님':'고정된 샘플 데이터'}<br><span class="tag">${state.mode==='real'?'REAL DATA':'DEMO MODE'}</span>`;
  document.querySelector('.footer').innerHTML = `<span>MARKET INTELLIGENCE / LOCAL</span><span>${state.mode==='real'?'실제 종가 · 기술 지표는 로컬 계산 · 개인 참고용':'DEMO / SAMPLE DATA · 실제 분석 아님'}</span>`;
}
function sourceNote(m, key=state.market) {
  return `<div class="note">출처: <a href="${sourceLinks[key]}" target="_blank" rel="noopener" style="color:var(--blue)">${esc(m?.attribution || (key==='sp'||key==='nq'?'FRED':'KRX OPEN API'))}</a><br>${esc(m?.copyright || '')}<br>${esc(availability(m))} · 마지막 수집 시도 ${esc(stamp(m?.last_attempt_at))}${m?.error?'<br><span class="neutral">'+esc(m.error)+'</span>':''}</div>`;
}
function unavailable(title, text) {
  return `<div class="card"><div class="row sectionhead"><h2>${title}</h2><span class="tag">NOT CONNECTED</span></div><div class="sub">${text}</div></div>`;
}
function realSpark(m) {
  if (!hasPrices(m)) return '<div class="spark sub" style="padding-top:15px">시세 데이터 없음</div>';
  const rows = m.history.slice(-30);
  return `<svg class="spark" viewBox="0 0 500 150" preserveAspectRatio="none" role="img" aria-label="최근 ${rows.length}거래일 실제 종가"><polyline points="${coords(rows.map(r=>r.close)).map(p=>p.join(',')).join(' ')}" fill="none" stroke="${m.change_pct>=0?'#55d9ad':'#f18891'}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}
function realIndicatorGroup(title, items) {
  return `<div class="card"><h2>${title}</h2>${items.map(([name,value,desc,status='계산값',color='neutral'])=>`<div class="indicator"><div class="row"><strong>${name}</strong><span class="badge ${color}">${value==='—'?'이용 불가':status}</span></div><div style="font-size:23px;margin-top:9px">${value}</div><p>${desc}</p></div>`).join('')}</div>`;
}
function realIndicatorCards() {
  const m=currentReal(), v=m?.indicators || {};
  const sma=n=>[`SMA ${n}`,number(v['sma'+n]),`최근 ${n}개 유효 종가의 단순 평균. 이력 부족 시 이용 불가.`,!Number.isFinite(v['sma'+n])?'이력 부족':m.value>=v['sma'+n]?'종가 ≥ 평균':'종가 < 평균',!Number.isFinite(v['sma'+n])?'neutral':m.value>=v['sma'+n]?'positive':'negative'];
  return `<div class="grid indicators">${realIndicatorGroup('Trend · 이동평균',[sma(20),sma(50),sma(200)])}${realIndicatorGroup('Momentum · 모멘텀',[
    ['RSI (14)',number(v.rsi14),'Wilder 평활. 70 이상 과열 / 30 이하 과매도 참고. 단독 매매 신호가 아닙니다.'],
    ['MACD (12, 26)',number(v.macd),'SMA로 초기화한 EMA(12) − EMA(26). 단위: 지수 포인트.'],
    ['MACD Signal (9)',number(v.macd_signal),'MACD의 EMA(9). 히스토그램 = MACD − Signal.'],
    ['MACD Histogram',number(v.macd_histogram),'신호선 대비 차이. 양·음수는 방향만 나타냅니다.'],
    ['5일 수익률',number(v.return5,'%'),'최근 5관측일 가격 수익률.'],
    ['60일 수익률',number(v.return60,'%'),'최근 60관측일 가격 수익률.'],
    ['20일 수익률',number(v.return20,'%'),'현재 종가 / 20관측일 전 종가 − 1. 배당 미포함 가격 수익률.']
  ])}${realIndicatorGroup('Risk · 변동성',[
    ['Realized Volatility (20)',number(v.realized_volatility20,'%'),'최근 20개 일별 로그 수익률의 표본 표준편차 × √252. 연율화.'],
    ['Realized Volatility (60)',number(v.realized_volatility60,'%'),'최근 60개 로그 수익률 표본 표준편차 × √252.'],
    ['52주 종가 고점 대비',number(v.drawdown52w,'%'),'마지막 관측일 전 52주(364일)의 종가 고점 대비. 52주 이력 부족 시 이용 불가.'],
    ...[20,50,200].map(n=>['SMA '+n+' 대비',number(v['distance_sma'+n],'%'),'종가 / 해당 이동평균 − 1.']),
    ['관측값 수',String(m?.history?.length || 0),'휴장일과 제공자 결측값은 채우지 않습니다. 누락된 거래일이 있으면 수익률 간격이 길어질 수 있습니다.']
  ])}</div>${sourceNote(m)}`;
}
const macroNames={vix:'VIX',us2y:'미국 국채 2년',us10y:'미국 국채 10년',yield_spread:'10Y − 2Y',hy_spread:'High Yield OAS',dxy:'ICE Dollar Index · DXY',broad_dollar:'연준 광의 달러 지수 · DXY 아님',usdkrw:'USD/KRW · 뉴욕 정오',bok_rate:'한국은행 기준금리',kr3y:'한국 국고채 3년'};
const macroMeaning={vix:'S&P 500 옵션의 30일 기대 변동성. 과거 실현변동성과 다릅니다.',us2y:'단기 정책금리 기대가 반영되는 2년 만기 국채 수익률.',us10y:'장기 금리 수준. 2년물과 관측일이 다를 수 있습니다.',yield_spread:'10년물 − 2년물 금리차. 음수이면 수익률곡선 역전입니다.',hy_spread:'하이일드 채권의 옵션 조정 신용 스프레드. 확대는 위험 프리미엄 증가를 뜻합니다.',dxy:'ICE의 통화 바스켓 달러 지수. 무료 권한이 없는 경우 비워 둡니다.',broad_dollar:'연준의 광의 무역가중 달러 지수. ICE DXY와 구성·기준이 다릅니다.',usdkrw:'달러 1단위당 원화. 상승은 이 시계열 기준 원화 약세를 뜻합니다.',bok_rate:'한국은행의 정책금리 수준. 관측일은 금리 결정 발표 시각이 아닙니다.',kr3y:'한국 국고채 3년 만기 수익률. 기준금리와 구분합니다.'};
const macroSymbols={vix:'VIXCLS',us2y:'DGS2',us10y:'DGS10',yield_spread:'T10Y2Y',hy_spread:'BAMLH0A0HYM2',broad_dollar:'DTWEXBGS',usdkrw:'DEXKOUS'};
function referencePanel(title, keys) {
  return `<div class="card"><div class="row"><h2>${title}</h2><span class="label">REAL DATA</span></div>${keys.map(k=>{
    const m=macroData?.indicators[k], available=Number.isFinite(m?.value);
    const status=!available?'이용 불가':m.status==='stale'?'수집 실패 · 이전 정상 값':(Date.now()-Date.parse(m.observation_date+'T00:00:00Z'))/86400000>14?'관측일 14일 초과 경과':'수집됨';
    const link=macroSymbols[k]?'https://fred.stlouisfed.org/series/'+macroSymbols[k]:k==='dxy'?'https://www.ice.com/fx-index':'https://ecos.bok.or.kr/';
    return `<div class="indicator"><div class="row"><strong>${esc(macroNames[k])}</strong><span>${number(m?.value)} <span class="tiny muted">${esc(m?.unit || '')}</span></span></div><div class="tiny ${status==='수집됨'?'muted':'neutral'}" style="margin-top:8px">${status} · 관측일 ${esc(m?.observation_date || '없음')}</div><div class="sub">전 관측 대비 ${number(m?.change)} ${esc(m?.change_unit || (m?.unit==='%'?'%p':m?.unit==='2006=100'?'pt':m?.unit||''))} ${m?.previous_observation_date?'('+esc(m.previous_observation_date)+')':''}</div><p class="sub">${esc(macroMeaning[k])}${k==='yield_spread'&&available?' 현재 '+(m.value<0?'역전':m.value>0?'양의 금리차':'평탄')+' 상태.':''}</p><details class="sub"><summary>출처·시각·이용 조건</summary><a href="${link}" target="_blank" rel="noopener" style="color:var(--blue)">${esc(m?.source||'미수집')}</a><br>로컬 정상 수집 ${esc(stamp(m?.updated_at))}<br>마지막 시도 ${esc(stamp(m?.last_attempt_at))}<br>실제 공개 시각: ${esc(m?.publication_date||'출처 응답에 없음 · 추정하지 않음')}<br>제공자 갱신 시각: ${esc(m?.source_updated_at||'미제공')}<br>${esc(m?.copyright||'')}<br>로컬 참고용 · 모델 학습 제외${m?.error?'<br>'+esc(m.error):''}</details></div>`;
  }).join('')}<div class="note">${macroLoadError?esc(macroLoadError)+'<br>':''}관측일 ≠ 공개일 ≠ 로컬 수집 시각. 서로 다른 관측일의 지표를 동시점 수치로 해석하지 마세요.</div></div>`;
}
function majorChanges() {
  return `<div class="card" style="margin-top:18px"><h2>Major Market Changes</h2><div class="sub">최신 관측값에서 계산한 변화 · 예측/뉴스 인과 분석 아님</div><div class="tablewrap"><table><thead><tr><th>시장 / 기준일</th><th>5일</th><th>20일</th><th>60일</th><th>52주 종가 고점 대비</th><th>60일 변동성</th></tr></thead><tbody>${Object.entries(markets).map(([k,m])=>{const v=realData?.markets[k]?.indicators||{};return `<tr><td>${m.name}<br><span class="tiny muted">${esc(realData?.markets[k]?.as_of||'없음')}</span></td>${['return5','return20','return60','drawdown52w','realized_volatility60'].map(key=>`<td class="${key.startsWith('return')?direction(v[key]):''}">${number(v[key],'%')}</td>`).join('')}</tr>`;}).join('')}</tbody></table></div></div>`;
}
function validateSnapshot(data) {
  if(data?.schema_version!==1 || !data.markets) throw Error('지원하지 않는 JSON 스키마');
  for(const key of Object.keys(markets)) {
    const m=data.markets[key];
    if(!m || !Array.isArray(m.history) || !['ok','stale','unavailable'].includes(m.status)) throw Error('시장 데이터 형식 오류');
    let previous='';
    for(const r of m.history) {
      if(!/^\d{4}-\d{2}-\d{2}$/.test(r.date) || r.date<=previous || !Number.isFinite(r.close) || r.close<=0) throw Error('가격 이력 검증 실패');
      previous=r.date;
    }
    if(hasPrices(m) && (m.as_of!==previous || m.value!==m.history[m.history.length-1].close || !Number.isFinite(m.change_pct) || !m.indicators || !m.updated_at)) throw Error('시장 요약 불일치');
    for(const n of Object.values(m.indicators||{})) if(n!==null && !Number.isFinite(n)) throw Error('지표 검증 실패');
    if(!hasPrices(m) && (m.value!==null || m.change_pct!==null || m.status!=='unavailable')) throw Error('이용 불가 상태 오류');
  }
  return data;
}
document.addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.dataset.chartPeriod) {state.chartPeriod=b.dataset.chartPeriod;render();}
  if(b.dataset.mode) {state.mode=b.dataset.mode;render();}
  if(b.hasAttribute('data-reload')) loadRealData();
});

document.addEventListener('change',e=>{
  if(e.target.dataset.sma) {
    const n=+e.target.dataset.sma;
    if(e.target.checked)state.smaLines.add(n);else state.smaLines.delete(n);
    render();
  }
});
async function readMacroData() {
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
  try {
    if(location.protocol==='file:')throw Error('거시 데이터는 로컬 서버가 필요합니다');
    const response=await fetch('data/reference_only/macro.json',{cache:'no-store',signal:controller.signal});
    if(!response.ok)throw Error('거시 JSON 미수집/읽기 실패 · python3 scripts/collect_macro.py');
    const data=validateMacro(await response.json());
    macroData=data;macroLoadError='';
  } catch(e){macroLoadError=e.name==='AbortError'?'거시 JSON 읽기 시간 초과':e.message;}
  finally {clearTimeout(timer);}
}
function validateMacro(data) {
  if(data?.schema_version!==1||data.usage_scope!=='local_reference_only'||data.ml_training_allowed!==false||!data.indicators)throw Error('거시 JSON 스키마 오류');
  for(const key of Object.keys(macroNames)) {
    const m=data.indicators[key];
    if(!m||!['ok','stale','unavailable'].includes(m.status)||!Array.isArray(m.history))throw Error('거시 지표 형식 오류');
    let prev='';
    for(const row of m.history){if(!/^\d{4}-\d{2}-\d{2}$/.test(row.date)||row.date<=prev||!Number.isFinite(row.close))throw Error('거시 이력 오류');prev=row.date;}
    if(m.history.length){if(!Number.isFinite(m.value)||m.observation_date!==prev||m.value!==m.history[m.history.length-1].close||!m.updated_at||m.status==='unavailable')throw Error('거시 요약 불일치');}
    else if(m.value!==null||m.status!=='unavailable')throw Error('거시 결측 상태 오류');
  }
  return data;
}

/* ---------- loading: price snapshot, reference macro and analysis JSON, then one render ---------- */
async function loadRealData() {
  if (loading) return;
  loading = true; render();
  // reference macro is optional: skip the request when status.json says it was never collected (no 404 noise)
  const side = loadIntel().then(() => intel.status?.reference_macro?.exists === false
    ? (macroData = null, macroLoadError = '참고 지표 미수집 · python3 scripts/collect_macro.py (선택)') : readMacroData());
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 10000);
  try {
    if (location.protocol === 'file:') throw Error('REAL DATA는 http://localhost:3000에서 열어 주세요');
    const response = await fetch('data/markets.json', {cache: 'no-store', signal: controller.signal});
    if (!response.ok) throw Error(`JSON 읽기 실패 (HTTP ${response.status})`);
    realData = validateSnapshot(await response.json()); loadError = '';
  } catch (error) { loadError = error.name === 'AbortError' ? 'JSON 읽기 시간 초과' : error.message; }
  finally { await side; clearTimeout(timer); loading = false; applyLatest(); render(); }
}

/* ---------- analysis views: regime, forecast, validation, news, briefing, system status ----------
   Reads data/dashboard/*.json written by scripts/mi_cli.py. Every panel states its source, as-of time and
   status; missing or unlicensed results are shown as unavailable, never replaced with sample values. */
const intelFiles = ['regime', 'macro', 'forecast', 'performance', 'news', 'briefing', 'status', 'latest'];
let intel = {}, intelErrors = {};
// generated_at older than this many days -> STALE badge (performance is retrained rarely, so no threshold)
const STALE_DAYS = {regime: 3, forecast: 3, briefing: 3, news: 3, status: 3, macro: 7};
const isObj = v => v !== null && typeof v === 'object' && !Array.isArray(v);
// minimal per-file shape check; a file that fails it is not loaded (the last good copy, if any, is kept)
const INTEL_SHAPE = {regime: d => isObj(d.markets), macro: d => isObj(d.series), forecast: d => isObj(d.markets),
  performance: d => isObj(d.markets), news: d => Array.isArray(d.items), briefing: d => isObj(d.sections) && Array.isArray(d.sources),
  status: d => isObj(d.prices), latest: d => isObj(d.markets)};
const FORMAT_ERR = '형식 오류';
Object.assign(state, {newsCat: 'All', newsMarket: 'all', perfModel: null});
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
const regTone = r => typeof r !== 'string' ? 'neutral' : r.startsWith('Bullish') ? 'positive' : r.startsWith('Bearish') ? 'negative' : 'neutral';
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
// latest.json: same-day closes (Toss, provisional) the price snapshot does not have yet -> cards, chart, indicators
function validLatest(L) {
  const h = L?.history;
  if (!isObj(L) || !Array.isArray(h) || h.length < 2 || !isObj(L.provisional)) return false;
  for (let i = 0; i < h.length; i++) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(h[i]?.date) || !Number.isFinite(h[i].close) || h[i].close <= 0 || (i && h[i].date <= h[i - 1].date)) return false;
  }
  return L.as_of === h[h.length - 1].date && L.value === h[h.length - 1].close && Number.isFinite(L.change_pct);
}
function applyLatest() {
  const latest = intel.latest?.markets;
  if (!isObj(latest) || !realData?.markets) return;
  for (const [k, L] of Object.entries(latest)) {
    const R = realData.markets[k];
    if (R && validLatest(L) && L.as_of > (R.as_of || '')) realData.markets[k] = {...R, ...L};
  }
}

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
const PERIODS = {'1M': 1, '3M': 3, '6M': 6, '1Y': 12, 'ALL': null};
const SMA_COLOR = {20: '#8baeff', 50: '#e6c17b', 200: '#c494e8'};
// Calendar window ending at the last observation; moving averages use the full history before the window (warm-up).
function chartSeries(m) {
  const full = m.history, last = new Date(full[full.length - 1].date + 'T00:00:00Z'), months = PERIODS[state.chartPeriod];
  let start = full[0].date;
  if (months) {
    const cutoff = new Date(Date.UTC(last.getUTCFullYear(), last.getUTCMonth() - months, 1));
    const end = new Date(Date.UTC(cutoff.getUTCFullYear(), cutoff.getUTCMonth() + 1, 0)).getUTCDate();
    cutoff.setUTCDate(Math.min(last.getUTCDate(), end));
    start = cutoff.toISOString().slice(0, 10);
  }
  const sums = [0];
  full.forEach(r => sums.push(sums[sums.length - 1] + r.close));
  return full.map((r, i) => ({...r, ma: Object.fromEntries([20, 50, 200].map(n => [n, i + 1 >= n ? (sums[i + 1] - sums[i + 1 - n]) / n : null]))}))
    .filter(r => r.date >= start);
}
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
  const rows = chartSeries(m), values = rows.map(r => r.close), first = values[0], lastV = values[values.length - 1];
  const all = [...values, ...rows.flatMap(r => [...state.smaLines].map(n => r.ma[n]).filter(Number.isFinite))];
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const y = v => 230 - ((v - lo) / span) * 200, x = i => i * 800 / Math.max(1, rows.length - 1);
  const line = key => rows.map((r, i) => { const v = key === 'close' ? r.close : r.ma[key]; return Number.isFinite(v) ? `${x(i)},${y(v)}` : null; }).filter(Boolean).join(' ');
  const last = rows[rows.length - 1];
  const periods = `<div class="ranges" aria-label="차트 기간">${Object.keys(PERIODS).map(p => `<button data-chart-period="${p}" class="${state.chartPeriod === p ? 'active' : ''}" aria-pressed="${state.chartPeriod === p}">${p}</button>`).join('')}</div>`;
  const toggles = `<div class="controls" style="margin-top:10px">${[20, 50, 200].map(n => `<label class="tiny" style="color:${SMA_COLOR[n]}"><input type="checkbox" data-sma="${n}" ${state.smaLines.has(n) ? 'checked' : ''}> SMA ${n}</label>`).join('')}</div>`;
  const points = rows.length <= 160 ? rows.map((r, i) => `<circle cx="${x(i)}" cy="${y(r.close)}" r="6" fill="transparent" tabindex="0" data-real-point="${i}" aria-label="${r.date} 종가 ${number(r.close)}"/>`).join('') : '';
  return `<div class="card"><div class="row"><div><h2>${esc(m.name)} 가격 추이</h2><div class="sub">${rows.length}관측일 · 일별 종가 · 보유 ${m.history.length}개 · 구간 수익률 ${sgn((lastV / first - 1) * 100, 2, '%')}</div></div>${periods}</div>${toggles}<div class="row" style="margin-top:14px"><span class="index" style="margin:0">${number(m.value)}</span><span class="${direction(m.change_pct)}">${signed(m.change_pct)}</span></div><div class="sub">구간 최고 ${number(Math.max(...values))} / 최저 ${number(Math.min(...values))} · ${[...state.smaLines].sort((a, b) => a - b).map(n => `SMA ${n} ${number(last.ma[n])}`).join(' / ') || '이동평균 숨김'}</div><div class="chartwrap"><div class="tooltip" role="status" style="white-space:normal;max-width:85%"></div><svg class="pricechart" viewBox="0 0 800 250" preserveAspectRatio="none" role="group" aria-label="${esc(m.name)} 실제 일별 종가 차트">${[30, 90, 150, 210].map(v => `<line x1="0" y1="${v}" x2="800" y2="${v}" stroke="#253040" stroke-dasharray="3 5"/>`).join('')}${[...state.smaLines].map(n => `<polyline points="${line(n)}" fill="none" stroke="${SMA_COLOR[n]}" stroke-width="1.5" stroke-dasharray="${n === 200 ? '5 3' : 'none'}" vector-effect="non-scaling-stroke"/>`).join('')}<polyline points="${line('close')}" fill="none" stroke="${lastV >= first ? '#55d9ad' : '#f18891'}" stroke-width="2" vector-effect="non-scaling-stroke"/>${points}</svg><div class="axis">${[0, Math.floor(rows.length / 3), Math.floor(rows.length * 2 / 3), rows.length - 1].map(i => `<span>${rows[i].date}</span>`).join('')}</div></div>${regimeStrip(rows.map(r => r.date))}${sourceNote(m)}</div>`;
}
function bindRealChart() {
  const chart = document.querySelector('.pricechart'), tip = document.querySelector('.tooltip');
  if (!chart || !tip || !hasPrices(currentReal())) return;
  const m = currentReal(), rows = chartSeries(m), provisional = m.provisional?.dates || [];
  const show = i => {
    i = Math.max(0, Math.min(rows.length - 1, i));
    tip.textContent = `${rows[i].date} · 종가 ${number(rows[i].close)}${provisional.includes(rows[i].date) ? ' · 잠정' : ''} · ${[20, 50, 200].map(n => 'SMA ' + n + ' ' + number(rows[i].ma[n])).join(' / ')}`;
    tip.style.display = 'block'; tip.style.left = `${Math.min(55, i / Math.max(rows.length - 1, 1) * 65)}%`;
  };
  const at = e => { const r = chart.getBoundingClientRect(); show(Math.round((e.clientX - r.left) / r.width * (rows.length - 1))); };
  chart.addEventListener('pointermove', at); chart.addEventListener('pointerdown', at);
  chart.addEventListener('pointerleave', () => { tip.style.display = 'none'; });
  document.querySelectorAll('[data-real-point]').forEach(el => {
    el.addEventListener('focus', () => show(+el.dataset.realPoint));
    el.addEventListener('blur', () => { tip.style.display = 'none'; });
  });
}
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
  return `<div class="card"><div class="row sectionhead"><h2>Market Regime &amp; Risk</h2>${withFlags('regime', pill('RULE-BASED', ''))}</div><div class="regime ${regTone(r.regime)}">${esc(REG_KO[r.regime] || r.regime)}</div><div class="sub">${esc(r.regime)} · ${esc(r.regime_since)}부터 ${r.regime_days}거래일 · 추세 점수 ${sgn(r.trend_score, 0)}/5${r.raw_regime !== r.regime ? `<br><span class="neutral">당일 원시 신호: ${esc(REG_KO[r.raw_regime] || r.raw_regime)} (전환 대기 ${Number.isFinite(r.pending_days) ? r.pending_days : '—'}/${esc(r.confirm_days)}일)</span>` : ''}</div>
  ${[['단기 추세 (20일선·5일)', r.short_term], ['중기 추세 (50/200일선·60일)', r.medium_term]].map(([a, b]) => `<div class="row riskrow"><span class="sub">${a}</span><span class="badge ${b === '상승' ? '' : b === '하락' ? 'negative' : 'neutral'}">${esc(b)}</span></div>`).join('')}
  <div class="row riskrow"><span class="sub">위험 수준</span><strong class="${g.score >= 50 ? 'negative' : g.score >= 25 ? 'neutral' : 'positive'}">${esc(g.level_ko)} · ${num(g.score, 1)}/100</strong></div>${g.components_available ? `<div class="tiny muted">구성요소 ${esc(g.components_available)}${Array.isArray(g.components_missing) && g.components_missing.length ? ` · 미수집 ${g.components_missing.map(c => esc(RISK_KO[c] || c)).join(', ')}` : ''}</div>` : ''}<div class="gauge" aria-hidden="true"><b style="left:calc(${Math.min(100, g.score || 0)}% - 1px)"></b></div>
  <div class="tiny muted">${Object.entries(g.components || {}).map(([k, v]) => `${esc(RISK_KO[k] || k)} ${v === null ? '미수집' : Math.round(v * 100)}`).join(' · ')}</div>
  ${dailyChanges(r)}
  <div class="note">출처 ${esc(r.source_id || '—')} · 기준 거래일 ${esc(r.as_of || '—')}<br>${(r.trend_rules || []).map(t => `${t.pass ? '✓' : '✕'} ${esc(t.label)}${Number.isFinite(t.value) ? ' ' + sgn(t.value, 2, '%') : ''}`).join(' &nbsp; ')}<br>국면은 현재 상태의 규칙 기반 서술이며 미래 수익률 예측이 아닙니다. ${genStamp(intel.regime)}</div></div>`;
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
// outcome-change explanation (forecast.json markets[k][h].explanation; only for validated models)
// accuracy against the user's own labels (news.json evaluation <- data/dashboard/nlp_eval.json)
function nlpEval(ev) {
  if (!isObj(ev)) return '정확도 평가: 라벨 없음 (<code>mi_cli.py label-export</code> → 라벨 입력 → <code>label-score</code>)<br>';
  const one = (name, r) => isObj(r) ? `${name} ${esc(r.verdict)} · 라벨 ${Number(r.n) || 0}개${Number.isFinite(r.accuracy) ? ' · 정확도 ' + pct(r.accuracy, 0) : ''}${Number.isFinite(r.macro_f1) ? ' · macro-F1 ' + num(r.macro_f1, 2) : ''}` : '';
  return `정확도 평가: ${one('분류', ev.category)} / ${one('감성', ev.sentiment)} <span class="muted">(${esc(ev.rule || '')})</span><br>`;
}
function explainBlock(e) {
  if (!isObj(e) || !Array.isArray(e.lines) || !e.lines.length) return '';
  return `<div class="riskrow" style="display:block;margin-top:14px"><span class="sub">전망 변화 설명 · ${esc(e.previous_cutoff)} → ${esc(e.cutoff)} 입력</span><ul class="brieflist" style="margin:6px 0 0">${e.lines.map(x => `<li>${esc(x)}</li>`).join('')}</ul><div class="tiny muted" style="margin-top:6px">${esc(e.note || '')}</div></div>`;
}
function probBars(f) {
  return [['상승 · Bullish', f.bullish_probability, 'positive', 'var(--green)'], ['횡보 · Neutral', f.neutral_probability, 'neutral', 'var(--amber)'], ['하락 · Bearish', f.bearish_probability, 'negative', 'var(--red)']]
    .map(([l, p, c, col]) => `<div class="prob-row"><div class="row"><span class="sub">${l}</span><span class="probvalue ${c}">${(p * 100).toFixed(0)}<small style="font-size:13px">%</small></span></div><div class="track"><i style="width:${p * 100}%;background:${col}"></i></div></div>`).join('');
}
function forecastCard() {
  const f = fc(), name = esc(markets[state.market].name);
  if (!intel.forecast) return unavailable('Market Forecast', missing('forecast', '예측'));
  if (!f || f.status === 'unavailable') {
    const g = intel.performance?.gate?.[state.market];
    return `<div class="card"><div class="row sectionhead"><h2>Market Forecast</h2>${withFlags('forecast', pill('UNAVAILABLE', 'bad'))}</div><div class="sub">${name} · ${horizon()}거래일 방향 확률을 제공하지 않습니다.</div><ul class="reasons"><li>${esc(f?.reason || '예측 기록 없음')}</li>${g && !g.allowed ? '<li>허용되지 않은 데이터로는 모델을 학습하지 않습니다. ML 학습 권한이 확인된 데이터를 <code>data/licensed/</code>에 등록하면 학습·검증이 활성화됩니다 (README 참고).</li>' : ''}</ul><div class="note">상승·횡보·하락 확률은 라이선스 확인 + 시간순 검증을 통과한 정량 모델에서만 표시됩니다. 임의·샘플 확률로 대체하지 않습니다.</div></div>`;
  }
  const validated = f.status === 'validated';
  return `<div class="card"><div class="row sectionhead"><h2>Market Forecast</h2>${withFlags('forecast', pill(validated ? 'VALIDATED' : 'NOT VALIDATED', validated ? 'ok' : 'warn'))}</div><div class="sub">${name} · ${f.horizon}거래일 · 기준 종가 ${esc(f.input_cutoff)} · 판정 예정 ${esc(f.expected_outcome_date)} (추정)</div>${f.provenance?.proxy ? `<div class="note" style="margin-top:10px;border:0;padding:0">ⓘ 지수 대신 추종 ETF <b>${esc(f.provenance.proxy)}</b>의 가격으로 학습·판정한 예측입니다 (지수 데이터는 ML 학습 불가). 지수와 ETF는 배당·보수만큼 차이가 납니다.</div>` : ''}${f.provenance?.provisional_input ? `<div class="note" style="margin-top:10px;border:0;padding:0">ⓘ 기준 종가는 토스증권 당일 시세(잠정치)이며, 다음 날 KRX 공식값으로 확정·대조됩니다.</div>` : ''}${f.timely === false ? `<div class="note neutral" style="margin-top:12px;border:0;padding:0">⚠ 늦은 예측 · 기준 종가(${esc(f.input_cutoff)}) 다음 거래일이 이미 개장한 뒤 생성되어 실시간 성과에서 제외됩니다. 한국 시장은 KRX 게시(익일 08:00) 후 개장 전(09:00)에 실행하세요.</div>` : ''}${validated ? '' : `<div class="note neutral" style="margin-top:12px;border:0;padding:0">⚠ 검증 기준 미충족 · 신뢰할 수 있는 예측이 아닙니다.<ul class="reasons">${(f.validation_reasons || []).map(r => `<li>${esc(r)}</li>`).join('')}</ul></div>`}<div style="${validated ? '' : 'opacity:.55'}">${probBars(f)}</div>${explainBlock(f.explanation)}<div class="note">모델 ${esc(MODEL_KO[f.model_name] || f.model_name)} · ${esc(f.model_version)}<br>횡보 범위 ±${(f.threshold * 100).toFixed(2)}% (0.43·σ60·√h, 사전 고정) · 대상 데이터 ${esc(f.provenance?.target_source)} · ${genStamp(intel.forecast)}</div></div>`;
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
  return `<div class="card" style="margin-top:18px"><div class="row sectionhead"><h2>${title}</h2>${pill('LIVE · 추가 전용', '')}</div>${list.length ? `<div class="tablewrap"><table><thead><tr><th>기준 종가일</th><th>모델 버전</th><th>상승</th><th>횡보</th><th>하락</th><th>검증</th><th>판정일</th><th>실제 수익률</th><th>결과</th></tr></thead><tbody>${list.map(r => `<tr><td>${esc(r.input_cutoff)}</td><td class="tiny">${esc(r.model_version)}</td><td>${pct(r.p_bull, 0)}</td><td>${pct(r.p_neutral, 0)}</td><td>${pct(r.p_bear, 0)}</td><td>${pill(r.validation_status, statusKind(r.validation_status))}</td><td>${esc(r.outcome_date || r.expected_outcome_date + ' (예정)')}</td><td>${Number.isFinite(r.actual_return) ? sgn(r.actual_return * 100, 2, '%') : '—'}</td><td class="${r.hit === null ? 'muted' : r.hit ? 'positive' : 'negative'}">${r.status === 'pending' ? 'Pending' : r.hit ? '✓ ' + esc(r.actual_class) : '✕ ' + esc(r.actual_class)}${r.late ? '<br><span class="tiny muted" title="다음 거래일 개장 이후에 기록되어 실시간 성과에서 제외">늦은 기록 · 집계 제외</span>' : ''}</td></tr>`).join('')}</tbody></table></div>` : '<div class="sub">이 시장·기간의 실시간 예측 기록이 없습니다. 예측은 생성 시점에 저장되며 이후 덮어쓰지 않습니다.</div>'}<div class="note">백테스트 결과는 Model Performance에서 별도 표시합니다. 판정은 h번째 다음 관측 종가와 예측 당시 저장한 횡보 임계값으로 계산합니다.</div></div>`;
}
function featurePanel() {
  const f = fc(), p = perf();
  if (!f || f.status === 'unavailable' || !p) return '';
  const imp = p.lightgbm_importance || [];
  return `<div class="card"><div class="row sectionhead"><h2>Forecast Drivers</h2><span class="label">${horizon()}D</span></div><div class="sub">LightGBM 정보이득 비중 (개발 구간 적합 · 설명용) 및 현재 입력값</div>${imp.slice(0, 8).map(r => `<div class="row riskrow"><span class="sub">${esc(r.feature)}</span><span><span class="tiny muted">현재 ${num(f.features?.[r.feature])}</span> &nbsp;<strong>${pct(r.gain_share)}</strong></span></div>`).join('') || '<div class="sub">중요도 정보 없음</div>'}<div class="note">변수 중요도는 인과 관계나 방향을 의미하지 않습니다. 제외된 입력: ${(p.dropped_features || []).map(d => esc(d.feature)).join(', ') || '없음'}</div></div>`;
}

// day-over-day block (regime.json markets[k].daily); absent in older files -> nothing shown
function dailyChanges(r) {
  const d = r.daily;
  if (!isObj(d)) return '';
  const changes = Array.isArray(d.changes) ? d.changes : [], expl = Array.isArray(d.explanation) ? d.explanation : [];
  return `<div class="riskrow" style="display:block"><span class="sub">전일(${esc(d.previous_as_of || '—')}) 대비</span><ul class="brieflist" style="margin:6px 0 0">${changes.length ? changes.map(c => `<li>${esc(c?.text)}</li>`).join('') : '<li class="muted">기준 이상 변화 없음</li>'}</ul></div>${expl.length ? `<details class="tiny muted" style="margin-top:8px"><summary>국면 판정 근거</summary>${expl.filter(x => !/^[✓✕]/.test(String(x))).map(x => `<div style="margin-top:4px">${esc(x)}</div>`).join('')}</details>` : ''}`;
}

/* ---------- briefing ---------- */
function briefingCard(full) {
  const b = intel.briefing;
  if (!b) return `<div class="card brief">${missing('briefing', '브리핑')}</div>`;
  const s = b.sections, li = a => `<ul class="brieflist">${(Array.isArray(a) ? a : []).map(x => `<li>${esc(x)}</li>`).join('')}</ul>`;
  const parts = [...(Array.isArray(s.daily_summary) && s.daily_summary.length ? [['0 · Daily Summary', li(s.daily_summary)]] : []), ['1 · Executive Summary', `<p>${esc(s.executive_summary)}</p>`], ['2 · US Market Overview', li(s.us_overview)], ['3 · Korea Market Overview', li(s.korea_overview)],
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
  else body = perfDetail(p) + econCard(p);
  return intelHeading('Model Performance', '시간순 walk-forward 백테스트 · 실시간 예측 성과', true) + body + liveSummaryCard() + liveHistoryTable(intel.forecast?.history) + licenseTable();
}
// cost-aware holdout evaluation of the primary model (performance.json markets[k][h].economics)
function econCard(p) {
  const e = p?.economics;
  if (!isObj(e)) return '';
  const label = {same_close: '당일 종가 진입', next_close: '하루 늦은 종가 진입 (시초가 진입의 보수적 대용)'};
  const rows = Object.entries(label).map(([k, title]) => {
    const r = e[k];
    if (!isObj(r) || r.status !== 'ok') return `<tr><td>${esc(title)}</td><td colspan="7" class="muted">${esc(r?.reason || '계산 불가')}</td></tr>`;
    const ci = Array.isArray(r.excess_ci90) ? r.excess_ci90 : [];
    return `<tr><td class="wrap">${esc(title)}</td><td>${Number(r.periods) || 0} / ${Number(r.trades) || 0}</td><td>${pct(r.hit_rate, 0)}</td><td class="${direction(r.strategy_total)}">${pct(r.strategy_total, 1)}</td><td class="${direction(r.buy_hold_total)}">${pct(r.buy_hold_total, 1)}</td><td>${pct(r.strategy_max_drawdown, 1)} / ${pct(r.buy_hold_max_drawdown, 1)}</td><td>${sgn((r.mean_excess_vs_hold ?? NaN) * 100, 3, '%p')} [${sgn((ci[0] ?? NaN) * 100, 3)}, ${sgn((ci[1] ?? NaN) * 100, 3)}]</td><td class="wrap" style="min-width:160px">${pill(r.verdict, r.verdict === '비용 차감 후 우위' ? 'ok' : 'warn')}</td></tr>`;
  }).join('');
  const any = Object.values(e).find(r => isObj(r) && r.status === 'ok');
  return `<div class="card" style="margin-top:18px"><div class="row sectionhead"><h2>비용 반영 성과 · 투자에 쓸 만한가</h2>${pill('HOLDOUT · 사전 고정 규칙', '')}</div><div class="tablewrap"><table><thead><tr><th>진입</th><th>기간 / 매수</th><th>적중률</th><th>전략 누적</th><th>단순 보유 누적</th><th>최대 낙폭 (전략 / 보유)</th><th>기간당 초과수익 [90% CI]</th><th>판정</th></tr></thead><tbody>${rows}</tbody></table></div><div class="note">규칙(2026-10-16 사전 고정): 주 모델이 '상승'을 가장 높게 보면 ${horizon()}거래일 보유, 아니면 현금 · 겹치지 않는 기간만 평가 · 왕복 비용 ${pct(any?.round_trip_cost, 2)} 차감 · 배당·세금·환전 미반영 · 단순 보유 대비 초과수익의 90% 하한이 0보다 클 때만 "비용 차감 후 우위"</div></div>`;
}
// strongest baseline comparison (optional key; shown next to vs_class_frequency when present)
function bestBase(b) {
  if (!isObj(b) || !b.baseline) return '';
  return `<br><span class="tiny muted">최강 기준 ${esc(MODEL_KO[b.baseline] || b.baseline)} 대비 LL ${sgn(b.log_loss_improvement, 3)} [${num(b.ci90_low, 4)}, ${num(b.ci90_high, 4)}] · Brier ${sgn(b.brier_improvement, 3)} (하한 ${num(b.brier_ci90_low, 4)})</span>`;
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
  <div class="grid kpis">${[['Accuracy', pct(m.accuracy), `기준 ${pct(base.accuracy)}`], ['Balanced Accuracy', pct(m.balanced_accuracy), `기준 ${pct(base.balanced_accuracy)}`], ['Brier Score ↓', num(m.brier, 3), `기준 ${num(base.brier, 3)} · skill ${sgn(m.vs_class_frequency.brier_skill * 100, 1, '%')}`], ['Log Loss ↓', num(m.log_loss, 3), `기준 ${num(base.log_loss, 3)} · 개선 90%CI [${num(m.vs_class_frequency.ci90_low, 4)}, ${num(m.vs_class_frequency.ci90_high, 4)}]${bestBase(m.vs_best_baseline)}`]].map(([a, b, c]) => `<div class="card"><div class="label">${a}</div><div class="kpi">${b}</div><div class="sub">${c}</div><div class="tiny muted" style="margin-top:8px">${esc(MODEL_KO[state.perfModel])} · n=${m.n}</div></div>`).join('')}</div>
  <div class="card"><div class="row sectionhead"><h2>Model Comparison</h2>${withFlags('performance', pill('HOLDOUT · 동일 fold', ''))}</div><div class="tablewrap"><table><thead><tr><th>모델</th><th>Accuracy</th><th>Balanced</th><th>Macro F1</th><th>Brier ↓</th><th>Log Loss ↓</th><th>ECE ↓</th><th>LL 개선 90%CI</th><th>보정</th><th>판정</th></tr></thead><tbody>${names.map(n => { const r = models[n], v = r.vs_class_frequency; return `<tr><td><button class="filter ${n === state.perfModel ? 'active' : ''}" data-perf-model="${esc(n)}" style="padding:5px 8px">${esc(MODEL_KO[n] || n)}</button></td><td>${pct(r.accuracy)}</td><td>${pct(r.balanced_accuracy)}</td><td>${num(r.macro_f1, 3)}</td><td>${num(r.brier, 3)}</td><td>${num(r.log_loss, 3)}</td><td>${num(r.ece, 3)}</td><td class="wrap">${n === 'class_frequency' ? '기준' : `[${num(v.ci90_low, 4)}, ${num(v.ci90_high, 4)}]${bestBase(r.vs_best_baseline)}`}</td><td>${esc(r.calibration)}</td><td>${pill(r.validation.status, statusKind(r.validation.status))}</td></tr>`; }).join('')}</tbody></table></div><div class="note">판정 기준(2026-10-15 사전 고정): 유효 holdout 표본 ≥ 50, 과거 클래스 빈도와 최강 기준 모델 <b>모두</b>에 대해 log loss·Brier 개선의 블록 부트스트랩 90% 하한 > 0, 균형 정확도 +2%p 이상, 최악 클래스 ECE ≤ 0.08, 개발→holdout log loss 악화 ≤ 10%. ${m.validation.reasons.length && state.perfModel !== 'class_frequency' ? '<br>' + esc(MODEL_KO[state.perfModel]) + ': ' + m.validation.reasons.map(esc).join(' · ') : ''}</div></div>
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
  return intelHeading('Market Indicators', '기술적·거시경제·위험 지표') + realIndicatorCards() + `<div class="grid two" style="margin-top:18px">${volChart}${macroPanel()}</div>` + (macroCards ? `<h2 style="margin:26px 0 14px">거시경제 지표 추이</h2><div class="grid indicators">${macroCards}</div>` : missing('macro', '거시 지표')) + `<h2 style="margin:26px 0 14px">참고 지표 (표시 전용 · 학습 제외 · 위 거시 지표와 겹치지 않는 항목만)</h2><div class="grid drivers">${referencePanel('Global Risk Indicators', ['vix', 'hy_spread', 'dxy'])}${referencePanel('Korea Rates', ['bok_rate', 'kr3y'])}</div>` + regHist;
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
  return intelHeading('News Intelligence', '공식 발표·뉴스 메타데이터 분석 (예측 모델과 분리)') + `<div class="card"><div class="row"><h2>News Sentiment</h2>${flags('news')}<span class="sub">${items.length}건 · 최근 14일 · ${genStamp(n)}</span></div>${scored ? `<div class="distribution">${counts.map((c, i) => `<span style="width:${c / scored * 100}%;background:${['var(--green)', 'var(--amber)', 'var(--red)'][i]}"></span>`).join('')}</div><div class="row"><span class="positive tiny">● 긍정 ${counts[0]}</span><span class="neutral tiny">● 중립 ${counts[1]}</span><span class="negative tiny">● 부정 ${counts[2]}</span></div>` : '<div class="sub" style="margin-top:12px">감성 점수 대상 없음</div>'}<div class="note">${esc(n.sentiment_note)} · ${esc(n.separation_note)}<br>${nlpEval(n.evaluation)}수집원: ${['fed_press_rss', 'sec_press_rss', 'gdelt_doc'].map(k => `${k} ${pill(rep[k]?.status || '—', statusKind(rep[k]?.status))}${rep[k]?.error ? ' <span class="tiny neutral">' + esc(rep[k].error) + '</span>' : ''}`).join(' · ')}<br>기사 본문은 저장하지 않습니다 (제목·URL·시각만). 원문은 링크로 확인하세요.</div></div>
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
  return intelHeading('System Status', '수집원·데이터 기준시각·모델·작업 이력', false) + `<div class="grid kpis">${[['마지막 수집', lj.ingest], ['마지막 학습', lj.train], ['마지막 예측', lj.forecast], ['마지막 브리핑', lj.briefing]].map(([a, j]) => `<div class="card"><div class="label">${a}</div><div style="font-size:17px;margin:12px 0 8px">${esc(j ? stamp(j.finished_at || j.started_at) : '실행 기록 없음')}</div>${j ? pill(j.status, statusKind(j.status)) : ''}<div class="sub" style="margin-top:8px">${esc(j?.message || '')}</div></div>`).join('')}</div>
  <div class="card"><div class="row sectionhead"><h2>가격 수집원</h2>${flags('status')}</div><div class="tablewrap"><table><thead><tr><th>시장</th><th>상태</th><th>기준일</th><th>정상 수집</th><th>마지막 시도</th><th>보관 행</th><th>오류</th></tr></thead><tbody>${pr}</tbody></table></div><div class="note">FRED 시세는 약관(저장·ML 금지) 때문에 SQLite에 보관하지 않고 가격 스냅샷(markets.json) 표시에만 씁니다. KRX는 공식 종가(다음 날 08:00 게시), 토스증권은 당일 종가(잠정치)와 SPY·QQQ를 제공합니다.</div></div>
  <div class="card" style="margin-top:18px"><h2>거시 지표 수집원</h2><div class="tablewrap"><table><thead><tr><th>시리즈</th><th>상태</th><th>최근 관측</th><th>보관 행</th><th>비고</th></tr></thead><tbody>${mc || '<tr><td colspan="5">수집 기록 없음</td></tr>'}</tbody></table></div></div>
  <div class="card" style="margin-top:18px"><h2>모델 학습 상태</h2>${Object.entries(g).map(([k, v]) => `<div class="row riskrow"><span class="sub">${esc(markets[k]?.name || k)} · ${esc(v.source_id)} · ${v.observations}관측</span>${v.allowed ? pill('학습 허용', 'ok') : `<span>${pill('학습 차단', 'bad')} <span class="tiny muted">${esc(v.reason)}</span></span>`}</div>`).join('') || '<div class="sub">학습 기록 없음</div>'}<div class="note">학습 시각 ${esc(stamp(s.model.trained_at))} · 검증 완료 조합 ${s.model.trained_pairs}개 · 실시간 예측 기록 ${s.forecast.live_records}건 (대기 ${s.forecast.pending})</div></div>
  <div class="card" style="margin-top:18px"><h2>작업 이력</h2><div class="tablewrap"><table><thead><tr><th>작업</th><th>시작</th><th>종료</th><th>상태</th><th>메시지</th></tr></thead><tbody>${s.jobs.slice(0, 20).map(j => `<tr><td>${esc(j.job)}</td><td>${esc(stamp(j.started_at))}</td><td>${esc(stamp(j.finished_at))}</td><td>${pill(j.status, statusKind(j.status))}</td><td class="wrap tiny">${esc(j.message || '')}</td></tr>`).join('')}</tbody></table></div><div class="note">자동 실행은 로컬 컴퓨터가 켜져 있을 때만 동작합니다. 상태 파일 생성 ${esc(stamp(s.generated_at))} · 엔진 ${esc(s.engine_version)}</div></div>` + licenseTable();
}

/* ---------- views ---------- */
function intelOverview() {
  return intelHeading('Daily Market Outlook', '시장 국면·위험·전망을 한눈에', true) + intelCards() + `<div class="grid two">${intelChart()}${regimeCard()}</div><div class="grid drivers">${forecastCard()}${macroPanel()}</div>` + factorCards() + majorChanges() + briefingCard(true);
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
referencePanel = guard('Reference Indicators', referencePanel); majorChanges = guard('Major Market Changes', majorChanges);
newsIntelView = guard('News Intelligence', newsIntelView, true); statusView = guard('System Status', statusView, true);
indicatorsView = guard('Market Indicators', indicatorsView, true);
function realView() {
  applyLatest();   // idempotent: newer provisional closes win whichever loader finished last
  const notice = loading ? '<div class="note" role="status">로컬 JSON을 읽고 있습니다…</div>' : loadError ? `<div class="card" role="alert" style="margin-top:18px;border-color:#695735">${esc(loadError)}${realData ? ' · 마지막으로 읽은 정상 JSON을 유지합니다.' : ' · 샘플 데이터로 대체하지 않습니다.'}<div class="sub">로컬 서버 실행 후 <code>.venv/bin/python scripts/mi_cli.py ingest</code> → JSON 다시 읽기</div></div>` : '';
  const views = {Overview: intelOverview, Forecast: intelForecast, Indicators: indicatorsView, 'News Intelligence': newsIntelView, 'Model Performance': perfView, 'System Status': statusView};
  return notice + (views[state.tab] || intelOverview)();
}
document.addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b || state.mode !== 'real') return;
  if (b.dataset.newsCat) { state.newsCat = b.dataset.newsCat; render(); }
  if (b.dataset.newsMarket) { state.newsMarket = b.dataset.newsMarket; render(); }
  if (b.dataset.perfModel) { state.perfModel = b.dataset.perfModel; render(); }
  if (b.dataset.market || b.dataset.horizon !== undefined) state.perfModel = null;
});
loadRealData();
