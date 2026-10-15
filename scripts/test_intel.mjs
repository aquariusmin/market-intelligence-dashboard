// Unit tests for intel.js pure helpers and loader (Node built-in test runner, no dependencies).
// Run: node --test scripts/test_intel.mjs
// The page scripts are evaluated as-is in one vm context (index.html inline script -> real-data.js -> intel.js)
// with a do-nothing DOM, so the tests exercise the shipped files without a build step or exports.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');

function load() {
  const noop = new Proxy(function () {}, {get: (t, k) => k === Symbol.toPrimitive ? () => '' : noop,
    apply: () => noop, construct: () => noop});
  const ctx = {console, document: noop, window: noop, navigator: noop, localStorage: noop, AbortController,
    setTimeout: (f, ms) => setTimeout(f, ms).unref(), clearTimeout, location: {protocol: 'http:'}, matchMedia: () => ({matches: false, addEventListener() {}}),
    fetch: () => new Promise(() => {})};   // initial page load never resolves: no render against the fake DOM
  vm.createContext(ctx);
  vm.runInContext(read('index.html').match(/<script>([\s\S]*?)<\/script>/)[1], ctx, {filename: 'index.html'});
  vm.runInContext('render = () => {}', ctx);   // page rendering needs a real DOM (covered by ui_smoke.py)
  for (const f of ['real-data.js', 'intel.js']) vm.runInContext(read(f), ctx, {filename: f});
  const api = vm.runInContext(`({esc, safeUrl, link, INTEL_SHAPE, flags, dailyChanges, explainBlock, nlpEval, loadIntel,
    getIntel: () => intel, getErrors: () => intelErrors, setIntel: v => { intel = v; }})`, ctx);
  return {ctx, api};
}

test('esc escapes every HTML-significant character', () => {
  const {api} = load();
  assert.equal(api.esc(`<img src=x onerror="a('b')">&`), '&lt;img src=x onerror=&quot;a(&#39;b&#39;)&quot;&gt;&amp;');
  assert.equal(api.esc(null), '');
});

test('safeUrl allows only http(s); links open with noopener', () => {
  const {api} = load();
  assert.equal(api.safeUrl('javascript:alert(1)'), '#');
  assert.equal(api.safeUrl('data:text/html,x'), '#');
  assert.equal(api.safeUrl('https://www.federalreserve.gov/a?b=1&c=2'), 'https://www.federalreserve.gov/a?b=1&amp;c=2');
  assert.match(api.link('https://x.example', '<b>t</b>'), /rel="noopener noreferrer".*&lt;b&gt;t&lt;\/b&gt;/);
});

test('INTEL_SHAPE rejects files without the expected top-level keys', () => {
  const {api} = load();
  assert.equal(api.INTEL_SHAPE.regime({markets: {}}), true);
  assert.equal(api.INTEL_SHAPE.regime({markets: []}), false);
  assert.equal(api.INTEL_SHAPE.briefing({sections: {}, sources: []}), true);
  assert.equal(api.INTEL_SHAPE.briefing({sections: {}}), false);
  assert.equal(api.INTEL_SHAPE.news({items: {}}), false);
});

test('loadIntel keeps the last good copy when a reload is malformed or fails', async () => {
  const {ctx, api} = load();
  const files = {regime: {generated_at: '2026-10-15T00:00:00Z', markets: {}}};
  ctx.fetch = async url => {
    const name = url.match(/dashboard\/(\w+)\.json/)[1];
    return name in files ? {ok: true, json: async () => files[name]} : {ok: false, status: 404};
  };
  await api.loadIntel();
  assert.deepEqual(Object.keys(api.getIntel()), ['regime']);
  files.regime = {generated_at: '2026-10-16T00:00:00Z', markets: 'broken'};
  await api.loadIntel();
  assert.equal(api.getIntel().regime.generated_at, '2026-10-15T00:00:00Z');   // previous copy kept
  assert.match(api.getErrors().regime, /형식 오류/);
  assert.match(api.flags('regime'), /재로드 실패/);
  assert.match(api.getErrors().news, /HTTP 404/);
});

test('STALE badge appears only after the per-file threshold', () => {
  const {api} = load();
  const at = days => new Date(Date.now() - days * 86400000).toISOString();
  api.setIntel({regime: {generated_at: at(1), markets: {}}, macro: {generated_at: at(5), series: {}}});
  assert.doesNotMatch(api.flags('regime'), /STALE/);
  assert.doesNotMatch(api.flags('macro'), /STALE/);     // macro threshold is 7 days
  api.setIntel({regime: {generated_at: at(4), markets: {}}});
  assert.match(api.flags('regime'), /STALE/);
});

test('daily changes are escaped and tolerate older files', () => {
  const {api} = load();
  assert.equal(api.dailyChanges({}), '');                                // regime.json before v0.4
  const html = api.dailyChanges({daily: {previous_as_of: '2026-10-13', changes: [{text: '<script>x</script>'}],
    explanation: ['추세 점수 +5/5']}});
  assert.match(html, /전일\(2026-10-13\) 대비/);
  assert.match(html, /&lt;script&gt;x&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(api.dailyChanges({daily: {changes: []}}), /기준 이상 변화 없음/);
  assert.equal(api.dailyChanges({daily: {changes: 'bad'}}).includes('기준 이상 변화 없음'), true);
});

test('outcome-change explanation renders only real lines, escaped', () => {
  const {api} = load();
  assert.equal(api.explainBlock(undefined), '');
  assert.equal(api.explainBlock({status: 'not_computed'}), '');
  const html = api.explainBlock({previous_cutoff: '2026-10-13', cutoff: '2026-10-14', lines: ['상승 30% → 33%', '<b>x</b>'], note: 'n'});
  assert.match(html, /전망 변화 설명 · 2026-10-13 → 2026-10-14/);
  assert.match(html, /&lt;b&gt;x&lt;\/b&gt;/);
});

test('news evaluation line handles missing and present reports', () => {
  const {api} = load();
  assert.match(api.nlpEval(null), /라벨 없음/);
  const html = api.nlpEval({rule: 'r', category: {verdict: '참고용', n: 12, accuracy: 0.75, macro_f1: 0.5}, sentiment: {verdict: '<i>', n: 0}});
  assert.match(html, /분류 참고용 · 라벨 12개 · 정확도 75%/);
  assert.match(html, /감성 &lt;i&gt;/);
});
