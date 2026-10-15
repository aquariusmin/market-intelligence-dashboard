/* v0.3: local JSON only. Original DEMO views/data stay isolated in index.html. */
state.mode = 'real';
state.chartPeriod = '3M';
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
  if (m.status === 'stale') return '수집 실패 · 이전 정상 데이터';
  const age = (Date.now()-Date.parse(m.as_of+'T00:00:00Z'))/86400000;
  return age > 4 ? '4일 이상 경과 · 휴장일 미반영' : '일별 종가 · 실시간 아님';
}
function realHeading(title, desc, controls=true) {
  const m = currentReal();
  return `<div class="heading"><div><h1>${title}</h1><div class="sub">${desc} · REAL DATA</div><div class="sub">${esc(markets[state.market].name)} 기준 거래일 ${esc(m?.as_of || '이용 불가')} ${m ? '('+esc(m.timezone)+')' : ''}<br>마지막 정상 수집 ${esc(stamp(m?.updated_at))}</div></div>${controls?`<div class="controls">${selector()}${state.tab==='Forecast'?segments():''}</div>`:''}</div>`;
}
function renderModeChrome() {
  document.querySelector('.topbar').innerHTML = `<span>Market Intelligence <span class="muted">/ v0.3</span></span><div class="controls"><div class="segments" aria-label="데이터 모드"><button data-mode="real" aria-pressed="${state.mode==='real'}" class="${state.mode==='real'?'active':''}">REAL DATA</button><button data-mode="demo" aria-pressed="${state.mode==='demo'}" class="${state.mode==='demo'?'active':''}">DEMO</button></div><button class="filter" data-reload ${loading?'disabled':''}>${loading?'로딩 중…':'JSON 다시 읽기'}</button></div>`;
  document.querySelector('.sidefoot').innerHTML = `<span class="statusdot"></span>Local v0.3<br>${state.mode==='real'?'일별 종가 · 실시간 아님':'고정된 샘플 데이터'}<br><span class="tag">${state.mode==='real'?'REAL DATA':'DEMO MODE'}</span>`;
  document.querySelector('.footer').innerHTML = `<span>MARKET INTELLIGENCE / LOCAL v0.3</span><span>${state.mode==='real'?'실제 종가 · 기술 지표는 로컬 계산 · 개인 참고용':'DEMO / SAMPLE DATA · 실제 분석 아님'}</span>`;
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
  if (m?.regime) return [m.regime.label + (m.regime.volatility?' · 변동성 '+m.regime.volatility:''), {uptrend:'positive',downtrend:'negative'}[m.regime.code] || 'neutral'];
  return ['분석 갱신 필요','neutral'];
}
function chartSeries(m) {
  const full=m.history, last=new Date(full[full.length-1].date+'T00:00:00Z');
  const months={'1M':1,'3M':3,'6M':6,'1Y':12}[state.chartPeriod];
  const cutoff=new Date(Date.UTC(last.getUTCFullYear(),last.getUTCMonth()-months,1));
  const end=new Date(Date.UTC(cutoff.getUTCFullYear(),cutoff.getUTCMonth()+1,0)).getUTCDate();
  cutoff.setUTCDate(Math.min(last.getUTCDate(),end));
  const start=cutoff.toISOString().slice(0,10), sums=[0];
  full.forEach(r=>sums.push(sums[sums.length-1]+r.close));
  return full.map((r,i)=>({...r,ma:Object.fromEntries([20,50,200].map(n=>[n,i+1>=n?(sums[i+1]-sums[i+1-n])/n:null]))})).filter(r=>r.date>=start);
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
  const m=currentReal();
  if(!hasPrices(m)) return unavailable(`${esc(markets[state.market].name)} 가격 추이`,esc(m?.error || '수집된 시세가 없습니다.'));
  const rows=chartSeries(m), values=rows.map(r=>r.close), colors={20:'#8baeff',50:'#e6c17b',200:'#c494e8'};
  const all=[...values,...rows.flatMap(r=>[...state.smaLines].map(n=>r.ma[n]).filter(Number.isFinite))];
  const lo=Math.min(...all),hi=Math.max(...all),range=hi-lo||1;
  const y=v=>230-((v-lo)/range)*200, x=i=>i*800/Math.max(1,rows.length-1);
  const line=key=>rows.map((r,i)=>Number.isFinite(key==='close'?r.close:r.ma[key])?`${x(i)},${y(key==='close'?r.close:r.ma[key])}`:null).filter(Boolean).join(' ');
  const last=rows[rows.length-1];
  return `<div class="card"><div class="row"><h2>${esc(m.name)} 가격 추이</h2><span class="label">REAL DATA</span></div><div class="controls" style="margin:16px 0"><div class="segments" aria-label="차트 기간">${['1M','3M','6M','1Y'].map(period=>`<button data-chart-period="${period}" class="${state.chartPeriod===period?'active':''}" aria-pressed="${state.chartPeriod===period}">${period}</button>`).join('')}</div>${[20,50,200].map(n=>`<label class="tiny" style="color:${colors[n]}"><input type="checkbox" data-sma="${n}" ${state.smaLines.has(n)?'checked':''}> SMA ${n}</label>`).join('')}</div><div class="row"><span class="index" style="margin:0">${number(m.value)}</span><span class="${direction(m.change_pct)}">${signed(m.change_pct)}</span></div><div class="sub" style="margin-top:8px">${rows[0].date} – ${last.date} · ${rows.length}관측일 · 기간은 마지막 관측일 기준<br>${[20,50,200].map(n=>`SMA ${n} ${number(last.ma[n])} (${number(m.indicators?.['distance_sma'+n],'%')})`).join(' / ')}</div><div class="chartwrap"><div class="tooltip" role="status" style="white-space:normal;max-width:85%"></div><svg class="pricechart" viewBox="0 0 800 250" preserveAspectRatio="none" role="group" aria-label="${esc(m.name)} 실제 일별 종가 차트">${[30,90,150,210].map(v=>`<line x1="0" y1="${v}" x2="800" y2="${v}" stroke="#253040" stroke-dasharray="3 5"/>`).join('')}${[...state.smaLines].map(n=>`<polyline points="${line(n)}" fill="none" stroke="${colors[n]}" stroke-width="1.5" stroke-dasharray="${n===200?'5 3':'none'}" vector-effect="non-scaling-stroke"/>`).join('')}<polyline points="${line('close')}" fill="none" stroke="${m.change_pct>=0?'#55d9ad':'#f18891'}" stroke-width="2.5" vector-effect="non-scaling-stroke"/>${rows.map((r,i)=>`<circle cx="${x(i)}" cy="${y(r.close)}" r="6" fill="transparent" tabindex="0" data-real-point="${i}" aria-label="${r.date} 종가 ${number(r.close)}"/>`).join('')}</svg><div class="axis"><span>${rows[0].date}</span><span>${rows[Math.floor(rows.length/2)].date}</span><span>${last.date}</span></div></div><div class="sub">차트 축 범위 ${number(lo)} – ${number(hi)} · 종가 기준</div>${sourceNote(m)}</div>`;
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
function macroPanel(title, keys) {
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
function realOverview() {
  const m=currentReal(),v=m?.indicators||{},[label,color]=trend(m);
  return realHeading('Daily Market Outlook','실제 시장 · 기술 추세 · 거시 환경')+realCards()+`<div class="grid two">${realChart()}<div class="card"><h2>Technical Trend</h2><div class="regime ${color}">${esc(label)}</div>${[['RSI (14)',number(v.rsi14)],['20일 수익률',number(v.return20,'%')],['20일 실현변동성',number(v.realized_volatility20,'%')],...[20,50,200].map(n=>['SMA '+n+' 대비',number(v['distance_sma'+n],'%')])].map(([a,b])=>`<div class="row riskrow"><span class="sub">${a}</span><strong>${b}</strong></div>`).join('')}<div class="note">상승: 종가 ≥ SMA20 ≥ SMA50 ≥ SMA200이고 20일 수익률 > 0. 하락: 종가 &lt; SMA20 &lt; SMA50 &lt; SMA200이고 수익률 &lt; 0. 나머지 혼조. 연율 변동성 &lt;20% 낮음 / ≥25% 높음. 규칙 기반 분류이며 예측 확률이 아닙니다.</div></div></div><div class="grid drivers">${macroPanel('Global Risk Indicators',['vix','us2y','us10y','yield_spread','hy_spread'])}${macroPanel('Macroeconomic Conditions',['dxy','broad_dollar','usdkrw','bok_rate','kr3y'])}</div>`+majorChanges()+`<div style="margin-top:18px">${realProbability()}</div>`;
}
function realView() {
  const notice=loading?'<div class="note" role="status">로컬 JSON을 읽고 있습니다…</div>':loadError?`<div class="card" role="alert" style="margin-top:18px;border-color:#695735">${esc(loadError)}${realData?' · 마지막으로 읽은 정상 JSON을 유지합니다.':' · 샘플 데이터로 대체하지 않습니다.'}<div class="sub">로컬 서버 실행 후 python3 scripts/collect_data.py → JSON 다시 읽기</div></div>`:'';
  let view;
  if(state.tab==='Overview') view=realOverview();
  else if(state.tab==='Forecast') view=realHeading('Market Forecast','실제 가격 이력 · 예측 모델 미연결')+`<div class="grid two">${realChart()}${realProbability()}</div>`;
  else if(state.tab==='Indicators') view=realHeading('Market Indicators','실제 종가에서 로컬 계산한 지표')+realIndicatorCards()+`<div class="grid drivers">${macroPanel('Global Risk Indicators',['vix','us2y','us10y','yield_spread','hy_spread'])}${macroPanel('Macroeconomic Conditions',['dxy','broad_dollar','usdkrw','bok_rate','kr3y'])}</div>`;
  else view=realHeading(state.tab,state.tab==='News Intelligence'?'뉴스 분석 미연결':'모델 학습·평가 미구현',false)+unavailable(state.tab,`v0.3 REAL DATA 모드에는 ${state.tab==='News Intelligence'?'실제 뉴스 수집·분석':'실제 모델·백테스트·성능 결과'}이 없습니다.<div style="margin-top:20px"><button class="filter" data-mode="demo">DEMO 화면 확인</button></div>`);
  return notice+view;
}
function bindRealChart() {
  const chart=document.querySelector('.pricechart'), tip=document.querySelector('.tooltip');
  if(!chart || !tip) return;
  const rows=chartSeries(currentReal());
  const show=i=>{i=Math.max(0,Math.min(rows.length-1,i));tip.textContent=`${rows[i].date} · 종가 ${number(rows[i].close)} · ${[20,50,200].map(n=>'SMA '+n+' '+number(rows[i].ma[n])).join(' / ')} · REAL DATA`;tip.style.display='block';tip.style.left=`${Math.min(55,i/(rows.length-1)*65)}%`;};
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
  const macroPromise=readMacroData();
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),10000);
  try {
    if(location.protocol==='file:') throw Error('REAL DATA는 http://localhost:3000에서 열어 주세요');
    const response=await fetch('data/markets.json',{cache:'no-store',signal:controller.signal});
    if(!response.ok) throw Error(`JSON 읽기 실패 (HTTP ${response.status})`);
    realData=validateSnapshot(await response.json());loadError='';
  } catch(error) {loadError=error.name==='AbortError'?'JSON 읽기 시간 초과':error.message;}
  finally {await macroPromise;clearTimeout(timer);loading=false;render();}
}
document.addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.dataset.chartPeriod) {state.chartPeriod=b.dataset.chartPeriod;render();}
  if(b.dataset.mode) {state.mode=b.dataset.mode;render();}
  if(b.hasAttribute('data-reload')) loadRealData();
});
loadRealData();

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
