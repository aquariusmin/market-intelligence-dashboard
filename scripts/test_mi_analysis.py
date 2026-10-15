"""v0.4 / v0.7 / v0.8 / v0.9 checks (offline). Synthetic inputs exist only inside these tests."""
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import collect_data
from mi import briefing, events, indicators, licenses, macro, news, prices, regime, store, validation
from test_mi_forecasting import bdays, rows, walk


class IndicatorParityTest(unittest.TestCase):
    def test_matches_v02_snapshot_formulas(self):
        r = rows(bdays(320), walk(320, 7))
        v02 = collect_data.indicators(r)
        df = indicators.frame([x['date'] for x in r], [x['close'] for x in r]).iloc[-1]
        for a, b in [('sma20', 'sma20'), ('sma200', 'sma200'), ('rsi14', 'rsi14'), ('macd', 'macd'),
                     ('macd_signal', 'macd_signal'), ('macd_histogram', 'macd_histogram'),
                     ('mom20', 'return20'), ('vol20', 'realized_volatility20')]:
            self.assertAlmostEqual(df[a], v02[b], places=8, msg=a)

    def test_drawdown_and_percentile_are_causal(self):
        s = pd.Series(walk(400, 8))
        s2 = s.copy()
        s2.iloc[300:] *= 3
        pd.testing.assert_series_equal(indicators.drawdown(s).iloc[:300], indicators.drawdown(s2).iloc[:300])
        pd.testing.assert_series_equal(indicators.trailing_percentile(s).iloc[:300],
                                       indicators.trailing_percentile(s2).iloc[:300])


class RegimeTest(unittest.TestCase):
    def series(self, closes):
        return rows(bdays(len(closes)), closes)

    def test_uptrend_downtrend_and_insufficient(self):
        up = np.exp(np.linspace(0, .6, 320) + np.random.default_rng(1).normal(0, .004, 320)) * 100
        r = regime.analyze('sp', self.series(up), {})
        self.assertEqual(r['status'], 'ok')
        self.assertTrue(r['regime'].startswith('Bullish'))
        down = up[::-1].copy()
        self.assertTrue(regime.analyze('sp', self.series(down), {})['regime'].startswith('Bearish'))
        self.assertEqual(regime.analyze('sp', self.series(up[:150]), {})['status'], 'insufficient_history')
        self.assertEqual(regime.analyze('sp', [], {})['status'], 'unavailable')

    def test_history_is_point_in_time(self):
        c = walk(400, 9)
        a = regime.analyze('sp', self.series(c), {}, history_len=400)
        c2 = c.copy()
        c2[350:] *= 0.5  # crash in the "future"
        b = regime.analyze('sp', self.series(c2), {}, history_len=400)
        ha = {h['date']: h['regime'] for h in a['history']}
        hb = {h['date']: h['regime'] for h in b['history']}
        cutoff = self.series(c)[349]['date']
        self.assertEqual({d: v for d, v in ha.items() if d <= cutoff}, {d: v for d, v in hb.items() if d <= cutoff})

    def test_confirmation_rule(self):
        raw = ['A', 'A', 'B', 'A', 'B', 'B', 'B', 'A']
        self.assertEqual(regime.confirm(raw, 3), ['A', 'A', 'A', 'A', 'A', 'A', 'B', 'B'])

    def test_risk_bands_and_korea_fx_component(self):
        comps = {'realized_vol': .9, 'drawdown': .8, 'vix': None}
        self.assertEqual(regime.risk_level(comps)[1], 'High')
        self.assertEqual(regime.risk_level({'a': .1})[1], 'Low')
        self.assertEqual(regime.risk_level({'a': None}), (None, None, None))
        row = pd.Series({'vol20_pct': .5, 'vol20': 15.0, 'drawdown252': -5.0})
        self.assertIn('krw', regime.risk_components(row, 'ks', {'USDKRW_ret20': 1.5}))
        self.assertNotIn('krw', regime.risk_components(row, 'sp', {'USDKRW_ret20': 1.5}))


class GateRuleTest(unittest.TestCase):
    def test_probability_only_gain_without_direction_is_not_validated(self):
        cfg = validation.Config(horizon=20)
        r = {'effective_n': 60, 'ece': .02, 'vs_class_frequency': {
            'ci90_low': .002, 'brier_skill': .007, 'balanced_accuracy_delta': 0.0}}
        g = validation.gate(r, 'momentum_rule', cfg)
        self.assertEqual(g['status'], 'not_validated')
        r['vs_class_frequency']['balanced_accuracy_delta'] = .05
        self.assertEqual(validation.gate(r, 'momentum_rule', cfg)['status'], 'validated')


class MacroTest(unittest.TestCase):
    DDP = ('"Series Description","10-year"\n"Unit:","Percent"\n"Multiplier:","1"\n"Currency:","NA"\n'
           '"Unique Identifier: ","H15/H15/RIFLGFCY10_N.B"\n"Time Period","RIFLGFCY10_N.B"\n'
           '2026-10-08,4.10\n2026-10-09,ND\n2026-10-12,4.20\n')

    def test_parse_skips_nd(self):
        self.assertEqual(macro.parse_ddp(self.DDP, 'RIFLGFCY10_N.B'), [('2026-10-08', 4.1), ('2026-10-12', 4.2)])
        with self.assertRaises(ValueError):
            macro.parse_ddp('<html>', 'X')
        with self.assertRaises(ValueError):
            macro.validate([('2026-10-08', 99.0)], macro.SERIES['UST10Y'], date(2026, 10, 15))

    def test_collect_archives_only_permitted_and_keeps_history_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'mi.sqlite'
            calls = []
            def ok(url):
                calls.append(url)
                assert 'fred' not in url, 'FRED-hosted series must not be requested by default'
                return self.DDP.replace('RIFLGFCY10_N.B', 'RIFLGFCY10_N.B","RIFLGFCY02_N.B","RIFLGFCM03_N.B') \
                    .replace('4.10\n', '4.10,3.9,3.8\n').replace('ND\n', 'ND,ND,ND\n').replace('4.20\n', '4.20,4.0,3.9\n')
            with store.connect(db) as conn:
                res, transient = macro.collect(conn, today=date(2026, 10, 15), request=ok, selected=['UST10Y', 'UST2Y', 'VIX'],
                                               first_start=date(2026, 10, 1), log=lambda *a: None, sleep=lambda s: None)
                self.assertEqual(res['UST10Y']['status'], 'ok')
                self.assertEqual(res['VIX']['status'], 'disabled')
                self.assertEqual(transient, {})
                self.assertEqual(len(calls), 1)  # one package request shared by both columns
                before = store.load_macro(conn, 'UST10Y')
                def fail(url): raise RuntimeError('offline')
                res2, _ = macro.collect(conn, today=date(2026, 10, 15), request=fail, selected=['UST10Y'],
                                        log=lambda *a: None, sleep=lambda s: None)
                self.assertEqual(res2['UST10Y']['status'], 'stale')
                self.assertEqual(store.load_macro(conn, 'UST10Y'), before)
                # revision is logged, not silently replaced
                store.upsert_macro(conn, 'UST10Y', [('2026-10-12', 4.25)], 'fed_h15')
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM observation_revisions').fetchone()[0], 1)

    def test_empty_ddp_retried_then_fails(self):
        n = []
        def empty(url):
            n.append(1)
            return ''
        with self.assertRaisesRegex(ValueError, 'DDP'):
            macro._fetch_chunked(macro.SERIES['UST10Y'], date(2026, 1, 1), date(2026, 2, 1), empty, lambda s: None)
        self.assertEqual(len(n), 4)


class PricesTest(unittest.TestCase):
    def test_fred_never_archived_krx_archived(self):
        snap = {'markets': {'sp': {'history': [{'date': '2026-10-14', 'close': 1.0}], 'status': 'ok'},
                            'ks': {'history': [{'date': '2026-10-14', 'close': 2.0}], 'status': 'ok'}}}
        with tempfile.TemporaryDirectory() as tmp, store.connect(Path(tmp) / 'db.sqlite') as conn:
            rep = prices.ingest_snapshot(conn, snap)
            self.assertIn('not_archived', rep['sp'])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM prices WHERE market='sp'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM prices WHERE market='ks'").fetchone()[0], 1)
            # archived history survives a shorter snapshot
            snap2 = {'markets': {'ks': {'history': [], 'status': 'stale'}}}
            s = prices.series(conn, 'ks', snap2, manifest={})
            self.assertEqual([r['close'] for r in s['rows']], [2.0])

    def test_licensed_manifest_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'manifest.json'
            p.write_text(json.dumps({'markets': {'sp': {'provider': 'x', 'ml_training': 'allowed'}}}))
            m = licenses.licensed_manifest(p)
            self.assertIn('terms_url', m['sp']['manifest_errors'])
            self.assertFalse(licenses.ml_allowed('licensed:sp', m)[0])


class NewsTest(unittest.TestCase):
    RSS = """<?xml version="1.0"?><rss><channel>
      <item><title>Federal Reserve issues FOMC statement</title><link>https://www.federalreserve.gov/a.htm</link>
        <pubDate>Wed, 14 Oct 2026 18:00:00 GMT</pubDate></item>
      <item><title>Federal Reserve issues FOMC statement</title><link>https://federalreserve.gov/a.htm/</link>
        <pubDate>Wed, 14 Oct 2026 18:00:00 GMT</pubDate></item>
      <item><title>No link</title></item></channel></rss>"""

    def test_parse_dedupe_classify(self):
        items = news.parse_rss(self.RSS, 'fed_press_rss')
        self.assertEqual(len(items), 2)
        kept = news.dedupe(items)
        self.assertEqual(len(kept), 1)  # same normalised URL
        a = {'title': 'Stocks rally as inflation cools sharply', 'url': 'https://a.example/1', 'published_at': '2026-10-14T10:00:00+00:00'}
        b = {'title': 'Stocks rally as inflation cools sharply today', 'url': 'https://b.example/2', 'published_at': '2026-10-14T12:00:00+00:00'}
        c = {'title': 'Stocks rally as inflation cools sharply', 'url': 'https://c.example/3', 'published_at': '2026-10-20T12:00:00+00:00'}
        out = news.dedupe([a, b, c])
        self.assertIsNone(out[0]['duplicate_of'])
        self.assertEqual(out[1]['duplicate_of'], news.news_id(a['url']))
        self.assertIsNone(out[2]['duplicate_of'])  # outside the 48h window
        self.assertEqual(news.categorize('FOMC holds interest rate steady'), 'Monetary Policy')
        self.assertEqual(news.categorize('CPI rises more than expected'), 'Inflation')
        self.assertEqual(news.categorize('기준금리 동결'), 'Monetary Policy')
        self.assertEqual(news.categorize('Gardening tips'), 'Other')

    def test_collect_stores_metadata_only_and_is_idempotent(self):
        gd = json.dumps({'articles': [{'url': 'https://x.example/n', 'title': 'Nasdaq tumbles on chip fears',
                                       'seendate': '20261014T120000Z', 'domain': 'x.example', 'language': 'English'}]})
        req = lambda url: gd if 'gdelt' in url else self.RSS
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp, store.connect(Path(tmp) / 'db.sqlite') as conn:
            r1 = news.collect(conn, request=req, sleep=lambda s: None, now=now, log=lambda *a: None)
            n1 = conn.execute('SELECT COUNT(*) FROM news').fetchone()[0]
            r2 = news.collect(conn, request=req, sleep=lambda s: None, now=now, log=lambda *a: None)
            self.assertEqual(r2['added'], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM news').fetchone()[0], n1)
            cols = [d[1] for d in conn.execute('PRAGMA table_info(news)')]
            self.assertFalse({'body', 'content', 'text'} & set(cols))
            row = conn.execute("SELECT * FROM news WHERE provider='gdelt_doc'").fetchone()
            self.assertEqual(row['sentiment_label'], 'negative')
            self.assertIn('nq', json.loads(row['related_markets']))
            self.assertEqual(r1['gdelt_doc']['status'], 'ok')

    def test_failed_source_does_not_block_others(self):
        def req(url):
            if 'sec.gov' in url:
                raise RuntimeError('HTTP 403')
            return self.RSS
        with tempfile.TemporaryDirectory() as tmp, store.connect(Path(tmp) / 'db.sqlite') as conn:
            r = news.collect(conn, request=req, use_gdelt=False, log=lambda *a: None,
                             now=datetime(2026, 10, 15, tzinfo=timezone.utc))
            self.assertEqual(r['sec_press_rss']['status'], 'failed')
            self.assertEqual(r['added'], 1)

    def test_korean_sentiment_not_scored(self):
        from mi import sentiment
        self.assertIsNone(sentiment.score('코스피 급락', 'ko')['label'])
        self.assertEqual(sentiment.score('Stocks plunge', 'en')['label'], 'negative')


class EventsBriefingTest(unittest.TestCase):
    HTML = ('<h4><a id="1">2026 FOMC Meetings</a></h4><div class="fomc-meeting__month col"><strong>October</strong></div>'
            '<div class="fomc-meeting__date col">27-28</div><div class="fomc-meeting__month col"><strong>Apr/May</strong></div>'
            '<div class="fomc-meeting__date col">30-1*</div>')

    def test_fomc_parse_and_failure(self):
        ev = events.parse_fomc(self.HTML)
        self.assertEqual([e['date'] for e in ev], ['2026-05-01', '2026-10-28'])
        up = events.upcoming(date(2026, 10, 15), request=lambda u: self.HTML)
        self.assertEqual(up['next_fomc']['date'], '2026-10-28')
        def boom(u): raise RuntimeError('offline')
        down = events.upcoming(date(2026, 10, 15), request=boom)
        self.assertEqual(down['status']['fomc'], 'failed')
        self.assertEqual(down['items'], [])

    def brief(self):
        r = regime.analyze('sp', rows(bdays(320), walk(320, 3)), {})
        r['source_id'] = 'test'
        fc = {'markets': {'sp': {'5': {'status': 'not_validated', 'bullish_probability': .5, 'neutral_probability': .3,
                                       'bearish_probability': .2, 'model_name': 'lightgbm'}},
                          'nq': {'5': {'status': 'unavailable', 'reason': 'licence'}}}}
        return briefing.build({'sp': r, 'ks': {'status': 'unavailable', 'reason': 'no key'}}, {}, fc, [], {'items': []})

    def test_rule_based_sections_and_probabilities_from_model(self):
        b = self.brief()
        s = b['sections']
        for k in ('executive_summary', 'us_overview', 'korea_overview', 'bullish_factors', 'bearish_factors',
                  'market_forecast', 'outlook_changes', 'events_to_watch'):
            self.assertIn(k, s)
        self.assertIn('상승 50%', s['market_forecast'][0])
        self.assertIn('미검증', s['market_forecast'][0])
        self.assertIn('licence', s['market_forecast'][1])
        self.assertEqual(b['method'], 'rule-based-v1')

    def test_llm_is_opt_in_and_validated(self):
        b = self.brief()
        with patch.dict('os.environ', {}, clear=True):
            same, status = briefing.llm_rewrite(b)
            self.assertTrue(status.startswith('skipped'))
            self.assertEqual(same, b)

        class Fake:
            def __init__(self, text):
                self.text = text
                self.beta = self
                self.messages = self

            def create(self, **kw):
                assert kw['fallbacks'] == 'default'
                block = type('B', (), {'type': 'text', 'text': self.text})
                return type('R', (), {'stop_reason': 'end_turn', 'content': [block]})
        bad, status = briefing.llm_rewrite(b, Fake('{"executive_summary": "S&P 500 상승 확률은 87%로 매우 높습니다. 강력 매수."}'))
        self.assertIn('fallback', status)
        self.assertEqual(bad['method'], 'rule-based-v1')
        good_text = '분석 가능한 시장의 국면과 위험 수준을 요약했습니다. 방향 확률은 검증된 정량 모델에서만 제공됩니다.'
        good, status = briefing.llm_rewrite(b, Fake(json.dumps({'executive_summary': good_text}, ensure_ascii=False)))
        self.assertEqual(status, 'ok')
        self.assertEqual(good['sections']['executive_summary'], good_text)
        self.assertEqual(good['sections']['market_forecast'], b['sections']['market_forecast'])


class JobLogTest(unittest.TestCase):
    def test_job_records_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'db.sqlite'
            with self.assertRaises(RuntimeError):
                with store.job('x', db):
                    raise RuntimeError('boom')
            with store.job('y', db) as r:
                r['message'] = 'fine'
            with store.connect(db) as conn:
                rows_ = [dict(r) for r in conn.execute('SELECT job, status, message FROM job_runs ORDER BY id')]
            self.assertEqual(rows_[0]['status'], 'failed')
            self.assertIn('boom', rows_[0]['message'])
            self.assertEqual(rows_[1], {'job': 'y', 'status': 'ok', 'message': 'fine'})


if __name__ == '__main__':
    unittest.main()
