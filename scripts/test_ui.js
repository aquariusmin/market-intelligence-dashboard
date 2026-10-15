// Dependency-free rendering/interaction regression tests. Synthetic inputs stay in memory.
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=path.join(__dirname,'..');
const history=Array.from({length:450},(_,i)=>({date:new Date(Date.UTC(2025,7,22+i)).toISOString().slice(0,10),close:100+i}));
const names={sp:'S&P 500',nq:'NASDAQ 100',ks:'KOSPI',kq:'KOSDAQ'};
const snapshot={schema_version:1,markets:Object.fromEntries(Object.entries(names).map(([k,name])=>[k,{name,status:'ok',as_of:history.at(-1).date,value:549,change_pct:1,history,indicators:{sma20:539.5,distance_sma20:1.76},updated_at:'2026-10-15T00:00:00Z',regime:{label:'상승 추세',code:'uptrend',volatility:'낮음'}}]))};
const macroKeys=['vix','us2y','us10y','yield_spread','hy_spread','dxy','broad_dollar','usdkrw','bok_rate','kr3y'];
const macro={schema_version:1,usage_scope:'local_reference_only',ml_training_allowed:false,indicators:Object.fromEntries(macroKeys.map(k=>[k,{name:k,status:k==='dxy'?'unavailable':'ok',history:k==='dxy'?[]:[{date:'2026-10-13',close:3},{date:'2026-10-14',close:4}],value:k==='dxy'?null:4,observation_date:k==='dxy'?null:'2026-10-14',publication_date:null,updated_at:'2026-10-15T00:00:00Z'}]))};
const nodes=Object.fromEntries(['nav','#app','.topbar','.sidefoot','.footer'].map(k=>[k,{innerHTML:''}])),handlers={};
const ctx={Date,console,AbortController,setTimeout,clearTimeout,location:{protocol:'http:'},fetch:async u=>({ok:true,json:async()=>JSON.parse(JSON.stringify(u.includes('macro')?macro:snapshot))}),document:{querySelector:s=>nodes[s]||null,querySelectorAll:()=>[],addEventListener:(t,f)=>(handlers[t]??=[]).push(f)}};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(root,'index.html'),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1],ctx);
vm.runInContext(fs.readFileSync(path.join(root,'real-data.js'),'utf8'),ctx);
(async()=>{
 await new Promise(r=>setImmediate(r));
 assert(nodes['#app'].innerHTML.includes('Global Risk Indicators'));
 assert(nodes['#app'].innerHTML.includes('Major Market Changes'));
 assert(nodes['#app'].innerHTML.includes('실제 공개 시각'));
 const lengths=[];
 for(const period of ['1M','3M','6M','1Y']){
  vm.runInContext(`state.chartPeriod='${period}';render()`,ctx);
  const rows=vm.runInContext('chartSeries(currentReal())',ctx);
  lengths.push(rows.length);
  assert.equal(rows.at(-1).ma[200],449.5); // Uses pre-window warmup, including in 1M.
  assert(nodes['#app'].innerHTML.includes('SMA 200'));
 }
 assert.deepEqual(lengths,[32,93,185,366]);
 const send=(type,target)=>handlers[type].forEach(f=>f({target}));
 send('click',{closest:()=>({dataset:{chartPeriod:'1M'},hasAttribute:()=>false})});
 assert.equal(vm.runInContext('state.chartPeriod',ctx),'1M');
 send('change',{dataset:{sma:'200'},checked:true});
 assert(vm.runInContext('state.smaLines.has(200)',ctx));
 send('change',{dataset:{sma:'200'},checked:false});
 assert(!vm.runInContext('state.smaLines.has(200)',ctx));
 for(const tab of ['Overview','Forecast','Indicators','News Intelligence','Model Performance']){
  vm.runInContext(`state.tab='${tab}';render()`,ctx);
  assert(!nodes['#app'].innerHTML.includes('5,842.47'));
 }
 ctx.fetch=async()=>{throw Error('offline')};
 await vm.runInContext('loadRealData()',ctx);
 assert.equal(vm.runInContext('realData.markets.sp.value',ctx),549);
 assert.equal(vm.runInContext('macroData.indicators.vix.value',ctx),4);
 const invalid=JSON.parse(JSON.stringify(macro));invalid.indicators.vix.value=8;
 ctx.invalid=invalid;assert.throws(()=>vm.runInContext('validateMacro(invalid)',ctx));
 vm.runInContext("state.mode='demo';state.tab='Overview';render()",ctx);
 assert(nodes['#app'].innerHTML.includes('5,842.47'));assert(!nodes['#app'].innerHTML.includes('Global Risk Indicators'));
 console.log('PASS 8 UI groups: macro render, date labels, 4 calendar windows, SMA warmup, controls, 5 tabs, failed reload retention, DEMO isolation');
})().catch(e=>{console.error(e);process.exitCode=1});
