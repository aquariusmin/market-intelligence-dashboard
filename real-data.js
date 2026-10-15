/* v0.2 base REAL DATA layer (prices + technical indicators). intel.js extends it for v1.0. DEMO views stay in index.html. */
state.mode = 'real';
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
  if (m.status === 'stale') return '수집 실패 · 이전 정상 데이터';
  const age = (Date.now()-Date.parse(m.as_of+'T00:00:00Z'))/86400000;
  return age > 4 ? '4일 이상 경과 · 휴장일 미반영' : '일별 종가 · 실시간 아님';
}
function realHeading(title, desc, controls=true) {
  const m = currentReal();
  return `<div class="heading"><div><h1>${title}</h1><div class="sub">${desc} · REAL DATA</div><div class="sub">${esc(markets[state.market].name)} 기준 거래일 ${esc(m?.as_of || '이용 불가')} ${m ? '('+esc(m.timezone)+')' : ''}<br>마지막 정상 수집 ${esc(stamp(m?.updated_at))}</div></div>${controls?`<div class="controls">${selector()}${state.tab==='Forecast'?segments():''}</div>`:''}</div>`;
}
function renderModeChrome() {
  document.querySelector('.topbar').innerHTML = `<span>Market Intelligence <span class="muted">/ v1.0 local</span></span><div class="controls"><div class="segments" aria-label="데이터 모드"><button data-mode="real" aria-pressed="${state.mode==='real'}" class="${state.mode==='real'?'active':''}">REAL DATA</button><button data-mode="demo" aria-pressed="${state.mode==='demo'}" class="${state.mode==='demo'?'active':''}">DEMO</button></div><button class="filter" data-reload ${loading?'disabled':''}>${loading?'로딩 중…':'JSON 다시 읽기'}</button></div>`;
  document.querySelector('.sidefoot').innerHTML = `<span class="statusdot"></span>Local v1.0<br>${state.mode==='real'?'일별 종가 · 실시간 아님':'고정된 샘플 데이터'}<br><span class="tag">${state.mode==='real'?'REAL DATA':'DEMO MODE'}</span>`;
  document.querySelector('.footer').innerHTML = `<span>MARKET INTELLIGENCE / LOCAL v1.0</span><span>${state.mode==='real'?'실제 종가 · 기술 지표는 로컬 계산 · 개인 참고용':'DEMO / SAMPLE DATA · 실제 분석 아님'}</span>`;
}
function sourceNote(m, key=state.market) {
  return `<div class="note">출처: <a href="${sourceLinks[key]}" target="_blank" rel="noopener" style="color:var(--blue)">${esc(m?.attribution || (key==='sp'||key==='nq'?'FRED':'KRX OPEN API'))}</a><br>${esc(m?.copyright || '')}<br>${esc(availability(m))} · 마지막 수집 시도 ${esc(stamp(m?.last_attempt_at))}${m?.error?'<br><span class="neutral">'+esc(m.error)+'</span>':''}</div>`;
}
function unavailable(title, text) {
  return `<div class="card"><div class="row sectionhead"><h2>${title}</h2><span class="tag">NOT CONNECTED</span></div><div class="sub">${text}</div></div>`;
}
function realProbability() {
  return unavailable('AI Market Forecast', `${esc(markets[state.market].name)} · ${[1,5,20][state.horizon]}거래일 전망<br>실제 예측 모델이 연결되지 않았습니다. 상승·횡보·하락 확률과 전망 변화는 제공하지 않습니다.<div class="note">기존 확률 예시는 상단 DEMO 모드에서 확인할 수 있습니다.</div>`);
}
function trend(m) {
  if (!hasPrices(m) || !Number.isFinite(m.indicators?.sma20) || !Number.isFinite(m.indicators?.return20)) return ['이용 불가','neutral'];
  if (m.value >= m.indicators.sma20 && m.indicators.return20 > 0) return ['상승 추세','positive'];
  if (m.value < m.indicators.sma20 && m.indicators.return20 < 0) return ['하락 추세','negative'];
  return ['혼조','neutral'];
}
function realSpark(m) {
  if (!hasPrices(m)) return '<div class="spark sub" style="padding-top:15px">시세 데이터 없음</div>';
  const rows = m.history.slice(-30);
  return `<svg class="spark" viewBox="0 0 500 150" preserveAspectRatio="none" role="img" aria-label="최근 ${rows.length}거래일 실제 종가"><polyline points="${coords(rows.map(r=>r.close)).map(p=>p.join(',')).join(' ')}" fill="none" stroke="${m.change_pct>=0?'#55d9ad':'#f18891'}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}
function realCards() {
  return `<div class="grid markets">${Object.entries(markets).map(([key,demo])=>{
    const m = realData?.markets[key], [label,color] = trend(m);
    return `<button class="card market ${key===state.market?'active':''}" data-market="${key}" aria-pressed="${key===state.market}"><div class="row"><h2>${demo.name}</h2><span class="label">${esc(m?.symbol || demo.ticker)}</span></div><div class="index">${number(m?.value)}</div><div class="tiny ${direction(m?.change_pct)}" style="margin-top:6px">${Number.isFinite(m?.change_pct)?(m.change_pct>=0?'▲ ':'▼ '):''}${signed(m?.change_pct)} <span class="muted">전 관측 종가 대비</span></div>${realSpark(m)}<div class="row"><span class="tiny muted">${esc(m?.as_of || '기준일 없음')}</span><span class="badge ${color}">${label}</span></div><div class="sub" style="margin-top:10px">${esc(availability(m))}</div></button>`;
  }).join('')}</div>`;
}
function realChart() {
  const m = currentReal();
  if (!hasPrices(m)) return unavailable(`${esc(markets[state.market].name)} 가격 추이`, esc(m?.error || '수집된 시세가 없습니다. 먼저 수집 명령을 실행하세요.'));
  const rows = m.history.slice(-120), values = rows.map(r=>r.close), pts=coords(values,800,230);
  const low = Math.min(...values), high=Math.max(...values);
  return `<div class="card"><div class="row"><div><h2>${esc(m.name)} 가격 추이</h2><div class="sub">최근 ${rows.length}관측일 · 종가 · 전체 보유 ${m.history.length}개<br>마우스 이동 / 터치 / 포인트 키보드 탐색</div></div><span class="label">REAL DATA</span></div><div class="row" style="margin-top:18px"><span class="index" style="margin:0">${number(m.value)}</span><span class="${direction(m.change_pct)}">${signed(m.change_pct)}</span></div><div class="sub">표시 구간 최고 ${number(high)} / 최저 ${number(low)}</div><div class="chartwrap"><div class="tooltip" role="status"></div><svg class="pricechart" viewBox="0 0 800 250" preserveAspectRatio="none" role="group" aria-label="${esc(m.name)} 실제 일별 종가 차트">${[30,90,150,210].map(y=>`<line x1="0" y1="${y}" x2="800" y2="${y}" stroke="#253040" stroke-dasharray="3 5"/>`).join('')}<polyline points="${pts.map(p=>p.join(',')).join(' ')}" fill="none" stroke="${m.change_pct>=0?'#55d9ad':'#f18891'}" stroke-width="2.5" vector-effect="non-scaling-stroke"/>${pts.map((p,i)=>`<circle cx="${p[0]}" cy="${p[1]}" r="6" fill="transparent" tabindex="0" data-real-point="${i}" aria-label="${rows[i].date} 종가 ${number(rows[i].close)}"/>`).join('')}</svg><div class="axis">${[0,Math.floor(rows.length/3),Math.floor(rows.length*2/3),rows.length-1].map(i=>`<span>${rows[i].date}</span>`).join('')}</div></div>${sourceNote(m)}</div>`;
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
    ['20일 수익률',number(v.return20,'%'),'현재 종가 / 20관측일 전 종가 − 1. 배당 미포함 가격 수익률.']
  ])}${realIndicatorGroup('Risk · 변동성',[
    ['Realized Volatility (20)',number(v.realized_volatility20,'%'),'최근 20개 일별 로그 수익률의 표본 표준편차 × √252. 연율화.'],
    ['거시경제 / VIX / Credit Spread','이용 불가','v0.2 수집 범위는 네 지수 종가입니다. 임의 대체값을 표시하지 않습니다.','미연결'],
    ['관측값 수',String(m?.history?.length || 0),'휴장일과 제공자 결측값은 채우지 않습니다. 누락된 거래일이 있으면 수익률 간격이 길어질 수 있습니다.']
  ])}</div>${sourceNote(m)}`;
}
function realOverview() {
  const m=currentReal(),v=m?.indicators||{},[label,color]=trend(m);
  return realHeading('Daily Market Outlook','실제 지수 종가와 기술적 분석')+realCards()+`<div class="grid two">${realChart()}<div class="card"><h2>Technical Snapshot</h2><div class="regime ${color}">${label}</div><div class="sub">SMA 20과 20일 수익률로 분류한 단순 규칙 · 예측 아님</div>${[['SMA 20',number(v.sma20)],['RSI (14)',number(v.rsi14)],['20일 수익률',number(v.return20,'%')],['실현변동성 (연율)',number(v.realized_volatility20,'%')]].map(([a,b])=>`<div class="row riskrow"><span class="sub">${a}</span><strong>${b}</strong></div>`).join('')}<div class="note">종가가 SMA 20 이상이고 20일 수익률이 양수면 상승 추세, 둘 다 반대면 하락 추세, 나머지는 혼조입니다. 확률·AI 분석이 아닙니다.</div></div></div><div class="grid drivers">${realProbability()}${unavailable('Market Risk Overview','VIX·국채금리·달러·환율 데이터는 아직 수집하지 않습니다. 실제 수집 지표는 Indicators에서 확인하세요.')}</div>${unavailable('Daily Market Brief / Market Drivers','실제 뉴스·AI 브리핑은 아직 연결되지 않았습니다. 기술 지표를 뉴스나 예측 근거로 해석하지 않습니다.')}`;
}
function realView() {
  const notice=loading?'<div class="note" role="status">로컬 JSON을 읽고 있습니다…</div>':loadError?`<div class="card" role="alert" style="margin-top:18px;border-color:#695735">${esc(loadError)}${realData?' · 마지막으로 읽은 정상 JSON을 유지합니다.':' · 샘플 데이터로 대체하지 않습니다.'}<div class="sub">로컬 서버 실행 후 python3 scripts/collect_data.py → JSON 다시 읽기</div></div>`:'';
  let view;
  if(state.tab==='Overview') view=realOverview();
  else if(state.tab==='Forecast') view=realHeading('Market Forecast','실제 가격 이력 · 예측 모델 미연결')+`<div class="grid two">${realChart()}${realProbability()}</div>`;
  else if(state.tab==='Indicators') view=realHeading('Market Indicators','실제 종가에서 로컬 계산한 지표')+realIndicatorCards();
  else view=realHeading(state.tab,state.tab==='News Intelligence'?'뉴스 분석 미연결':'모델 학습·평가 미구현',false)+unavailable(state.tab,`v0.2 REAL DATA 모드에는 ${state.tab==='News Intelligence'?'실제 뉴스 수집·분석':'실제 모델·백테스트·성능 결과'}이 없습니다.<div style="margin-top:20px"><button class="filter" data-mode="demo">DEMO 화면 확인</button></div>`);
  return notice+view;
}
function bindRealChart() {
  const chart=document.querySelector('.pricechart'), tip=document.querySelector('.tooltip');
  if(!chart || !tip) return;
  const rows=currentReal().history.slice(-120);
  const show=i=>{i=Math.max(0,Math.min(rows.length-1,i));tip.textContent=`${rows[i].date} · 종가 ${number(rows[i].close)} · REAL DATA`;tip.style.display='block';tip.style.left=`${Math.min(55,i/(rows.length-1)*65)}%`;};
  chart.addEventListener('pointermove',e=>{const r=chart.getBoundingClientRect();show(Math.round((e.clientX-r.left)/r.width*(rows.length-1)));});
  chart.addEventListener('pointerleave',()=>{tip.style.display='none';});
  chart.addEventListener('pointerdown',e=>{const r=chart.getBoundingClientRect();show(Math.round((e.clientX-r.left)/r.width*(rows.length-1)));});
  document.querySelectorAll('[data-real-point]').forEach(el=>{
    el.addEventListener('focus',()=>show(+el.dataset.realPoint));
    el.addEventListener('blur',()=>{tip.style.display='none';});
  });
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
async function loadRealData() {
  if(loading) return;
  loading=true;render();
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),10000);
  try {
    if(location.protocol==='file:') throw Error('REAL DATA는 http://localhost:3000에서 열어 주세요');
    const response=await fetch('data/markets.json',{cache:'no-store',signal:controller.signal});
    if(!response.ok) throw Error(`JSON 읽기 실패 (HTTP ${response.status})`);
    realData=validateSnapshot(await response.json());loadError='';
  } catch(error) {loadError=error.name==='AbortError'?'JSON 읽기 시간 초과':error.message;}
  finally {clearTimeout(timer);loading=false;render();}
}
document.addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.dataset.mode) {state.mode=b.dataset.mode;render();}
  if(b.hasAttribute('data-reload')) loadRealData();
});
loadRealData();
