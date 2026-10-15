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
from mi import backfill, briefing, daily, events, forecasting, indicators, licenses, macro, news, prices, regime, store, validation
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

    def test_risk_reports_component_coverage(self):
        up = np.exp(np.linspace(0, .6, 320) + np.random.default_rng(1).normal(0, .004, 320)) * 100
        risk = regime.analyze('sp', self.series(up), {})['risk']
        self.assertEqual(risk['components_available'], '2/5')
        self.assertEqual(risk['components_missing'], ['vix', 'credit', 'rates'])
        self.assertIn('components', risk)
        ks = regime.analyze('ks', self.series(up), {})['risk']
        self.assertEqual(ks['components_available'], '2/6')
        self.assertIn('krw', ks['components_missing'])

    def row(self, **kw):
        base = dict(close=100.0, sma200=95.0, dist_sma200=5.26, mom20=1.0, vol20_pct=.5, vol20=15.0,
                    drawdown252=-2.0, rsi14=55.0)
        return pd.Series(dict(base, **kw))

    def test_vol_factor_at_one_year_high(self):
        _, bear = regime.factors(self.row(vol20_pct=1.0, vol20=31.0), None, 'sp')
        self.assertIn('20일 실현변동성 31.0% · 최근 1년 최고 수준', bear)
        self.assertFalse(any('상위 0%' in f for f in bear))
        _, bear = regime.factors(self.row(vol20_pct=.8), None, 'sp')
        self.assertIn('20일 실현변동성 15.0% · 최근 1년 중 상위 20%', bear)

    def test_rate_factor_matches_risk_definition_and_shows_reference_date(self):
        for chg, txt in ((-0.30, '-30bp'), (0.28, '+28bp')):
            bull, bear = regime.factors(self.row(), {'UST10Y_chg20': chg, 'UST10Y_obs': '2026-10-13'}, 'sp')
            self.assertFalse(any('금리' in f for f in bull))  # a large move either way is rate-volatility stress
            self.assertIn('미국 10년물 금리 20일간 %s · 금리 변동성 확대 (기준 10-13)' % txt, bear)
        d = bdays(320)
        ust = {'UST10Y': [(x.isoformat(), 4.0 + 0.02 * i) for i, x in enumerate(d)]}
        r = regime.analyze('sp', self.series(walk(320, 3)), ust)
        rate = [f for f in r['bearish_factors'] if '10년물' in f]
        self.assertEqual(len(rate), 1)
        self.assertRegex(rate[0], r'\(기준 \d{2}-\d{2}\)$')


class GateRuleTest(unittest.TestCase):
    def test_probability_only_gain_without_direction_is_not_validated(self):
        cfg = validation.Config(horizon=20)
        r = {'effective_n': 60, 'ece': .02, 'vs_class_frequency': {
            'ci90_low': .002, 'brier_ci90_low': .001, 'brier_skill': .007, 'balanced_accuracy_delta': 0.0}}
        g = validation.gate(r, 'momentum_rule', cfg)
        self.assertEqual(g['status'], 'not_validated')
        r['vs_class_frequency']['balanced_accuracy_delta'] = .05
        self.assertEqual(validation.gate(r, 'momentum_rule', cfg)['status'], 'validated')

    def test_model_worse_than_strongest_baseline_is_not_validated(self):
        cfg = validation.Config(horizon=5)
        good = {'ci90_low': .01, 'brier_ci90_low': .002, 'brier_skill': .02, 'balanced_accuracy_delta': .05}
        r = {'effective_n': 60, 'ece': .02, 'log_loss': 1.0, 'dev_log_loss': 1.0, 'vs_class_frequency': dict(good),
             'vs_best_baseline': dict(good, baseline='momentum_rule')}
        self.assertEqual(validation.gate(r, 'lightgbm', cfg)['status'], 'validated')
        r['vs_best_baseline'].update(ci90_low=-.004, brier_ci90_low=-.001)
        g = validation.gate(r, 'lightgbm', cfg)
        self.assertEqual(g['status'], 'not_validated')
        self.assertTrue(any('momentum_rule' in x for x in g['reasons']))
        # and a large development -> holdout degradation is flagged as overfitting
        r['vs_best_baseline'].update(ci90_low=.01, brier_ci90_low=.002)
        r['log_loss'] = 1.2
        self.assertIn('과적합', ' '.join(validation.gate(r, 'lightgbm', cfg)['reasons']))

    def test_gate_settings_cannot_be_overridden(self):
        with self.assertRaises(ValueError):
            forecasting.train(None, cfg_overrides={'max_ece': 0.5})

    def test_ece_reports_worst_class(self):
        p = np.tile([0.2, 0.6, 0.2], (100, 1))
        y = np.array([1] * 60 + [0] * 40)   # neutral perfectly calibrated, bearish/bullish off by 0.2
        self.assertAlmostEqual(validation.reliability(p, y)[1], 0.2, places=9)


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

    def test_classification_word_boundaries(self):
        # former false positives from open-ended \w* suffixes and the bare 경기 keyword
        self.assertNotEqual(news.categorize('An important update for shareholders'), 'Trade')
        self.assertNotEqual(news.categorize('Warner Bros shares climb'), 'Geopolitics')
        self.assertNotEqual(news.categorize('경기도 아파트 분양 일정'), 'Economic Growth')
        self.assertEqual(news.categorize('Warner Bros shares climb'), 'Other')
        # true positives (English inflections + Korean compounds)
        self.assertEqual(news.categorize('US imposes new tariffs on steel imports'), 'Trade')
        self.assertEqual(news.categorize('Korea exports rise for a third month'), 'Trade')
        self.assertEqual(news.categorize('War risk lifts oil prices'), 'Geopolitics')
        self.assertEqual(news.categorize('Geopolitical tensions weigh on Asia'), 'Geopolitics')
        self.assertEqual(news.categorize('SEC charges adviser with fraud'), 'Financial Stability')
        self.assertEqual(news.categorize('경기 둔화 우려 확산'), 'Economic Growth')
        self.assertEqual(news.categorize('경기침체 신호'), 'Economic Growth')
        self.assertEqual(news.categorize('반도체 수출 증가'), 'Trade')

    def test_importance_age_penalty_applied_on_read(self):
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp, store.connect(Path(tmp) / 'db.sqlite') as conn:
            news.collect(conn, request=lambda u: self.RSS, use_gdelt=False, now=now, log=lambda *a: None)
            stored = conn.execute('SELECT importance FROM news').fetchone()[0]
            self.assertEqual(stored, 5)  # Monetary Policy (3) + official (1) + 1, no age penalty stored
            fresh = news.recent(conn, now=now)[0]
            self.assertEqual((fresh['importance'], fresh['base_importance']), (5, 5))
            old = news.recent(conn, now=datetime(2026, 10, 20, tzinfo=timezone.utc))[0]
            self.assertEqual((old['importance'], old['base_importance']), (4, 5))
            self.assertEqual(conn.execute('SELECT importance FROM news').fetchone()[0], 5)  # history unchanged

    def test_gdelt_429_stops_remaining_queries(self):
        calls = []

        def req(url):
            if 'gdelt' in url:
                calls.append(url)
                raise RuntimeError('데이터 요청 실패 (HTTP 429, curl code 0) · 요청 한도 초과')
            return self.RSS
        with tempfile.TemporaryDirectory() as tmp, store.connect(Path(tmp) / 'db.sqlite') as conn:
            r = news.collect(conn, request=req, sleep=lambda s: None, log=lambda *a: None,
                             now=datetime(2026, 10, 15, tzinfo=timezone.utc))
        self.assertEqual(len(calls), 1)
        self.assertEqual(r['gdelt_doc']['status'], 'failed')
        self.assertIn('429', r['gdelt_doc']['error'])

    def test_unlicensed_provider_is_not_stored(self):
        gd = json.dumps({'articles': [{'url': 'https://x.example/n', 'title': 'Nasdaq tumbles on chip fears',
                                       'seendate': '20261014T120000Z', 'domain': 'x.example', 'language': 'English'}]})
        req = lambda url: gd if 'gdelt' in url else self.RSS
        with tempfile.TemporaryDirectory() as tmp, store.connect(Path(tmp) / 'db.sqlite') as conn, \
                patch('mi.licenses.storage_allowed', side_effect=lambda sid, manifest=None: sid != 'gdelt_doc'):
            r = news.collect(conn, request=req, sleep=lambda s: None, log=lambda *a: None,
                             now=datetime(2026, 10, 15, tzinfo=timezone.utc))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM news WHERE provider='gdelt_doc'").fetchone()[0], 0)
            self.assertEqual(r['added'], 1)
        self.assertEqual(r['skipped_unlicensed'], 1)  # both GDELT queries return it; URL dedupe keeps one
        self.assertEqual(r['gdelt_doc']['storage'], 'skipped')
        self.assertEqual(r['gdelt_doc']['status'], 'ok')

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

    def test_english_sentiment_negation(self):
        from mi import sentiment
        self.assertEqual(sentiment.score('Fed delivers rate cut', 'en')['label'], 'positive')
        no_cut = sentiment.score('Fed signals no rate cut this year', 'en')
        self.assertEqual(no_cut['label'], 'negative')
        self.assertEqual(no_cut['method'], 'lexicon-en-v2')  # still the low-confidence heuristic
        self.assertEqual(sentiment.score('Earnings not weak after all', 'en')['label'], 'positive')
        self.assertEqual(sentiment.score('Markets rally without fear', 'en')['label'], 'positive')
        self.assertEqual(sentiment.score("Margins don't improve", 'en')['label'], 'negative')


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

    def test_fomc_meeting_spanning_year_end(self):
        html = ('<h4>2026 FOMC Meetings</h4><div class="fomc-meeting__month"><strong>December</strong></div>'
                '<div class="fomc-meeting__date">8-9*</div><div class="fomc-meeting__month"><strong>Dec/Jan</strong></div>'
                '<div class="fomc-meeting__date">31-1</div>'
                '<h4>2027 FOMC Meetings</h4><div class="fomc-meeting__month"><strong>January</strong></div>'
                '<div class="fomc-meeting__date">26-27</div>')
        self.assertEqual([e['date'] for e in events.parse_fomc(html)], ['2026-12-09', '2027-01-01', '2027-01-27'])

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

    def test_restricted_build_keeps_labels_but_no_price_derived_numbers(self):
        r = regime.analyze('sp', rows(bdays(320), walk(320, 3)), {})
        r['source_id'] = 'fred_sp500'
        shown = briefing.build({'sp': r}, {}, {}, [], {'items': []})
        kept = briefing.build({'sp': r}, {}, {}, [], {'items': []}, restricted={'sp'})
        self.assertIn('20거래일 수익률', shown['sections']['us_overview'][0])
        para = kept['sections']['us_overview'][0]
        self.assertIn(briefing.REGIME_KO[r['regime']], para)
        self.assertNotIn('%', para)
        self.assertNotIn('%s점' % r['risk']['score'], kept['sections']['executive_summary'])
        withheld = [f for f in kept['sections']['bullish_factors'] + kept['sections']['bearish_factors']
                    if f.startswith('S&P 500 ·')]
        self.assertTrue(withheld and all('보관하지 않음' in f and '%' not in f for f in withheld))
        self.assertNotIn('뚜렷한', ' '.join(kept['sections']['bullish_factors'] if r['bullish_factors'] else []))
        self.assertEqual(kept['redacted_markets'], ['sp'])
        self.assertNotIn('redacted_markets', shown)

    def test_missing_vol_is_unavailable_not_zero(self):
        r = regime.analyze('sp', rows(bdays(320), walk(320, 3)), {})
        r['metrics']['vol20'] = None
        p = briefing.market_paragraph('sp', r)
        self.assertIn('20일 실현변동성 이용 불가', p)
        self.assertNotIn('0.0%', p)

    def test_news_newest_first_and_age_at_read_time(self):
        mk = lambda i, pub, imp: {'title': 't%d' % i, 'url': 'https://e.example/%d' % i, 'source': 's',
                                  'category': 'Inflation', 'importance': imp, 'base_importance': imp,
                                  'published_at': pub}
        items = [mk(1, '2026-10-13T00:00:00+00:00', 4), mk(2, '2026-10-14T00:00:00+00:00', 4),
                 mk(3, '2026-10-01T00:00:00+00:00', 4), mk(4, '2026-10-14T06:00:00+00:00', 5)]
        b = briefing.build({}, {}, {}, items, {'items': []}, now=datetime(2026, 10, 15, tzinfo=timezone.utc))
        got = [(n['title'], n['importance']) for n in b['sections']['news']]
        self.assertEqual(got, [('t4', 5), ('t2', 4), ('t1', 4), ('t3', 3)])  # t3 is >72h old at read time

    FACTS = json.dumps({'us_overview': [
        'S&P 500는 2026-10-14 기준 상승 추세 · 낮은 변동성 국면입니다 (추세 점수 +3/5, 12일째). 20거래일 수익률 +2.21%, '
        '60거래일 +4.10%, 20일 실현변동성 12.3%, 52주 고점 대비 -1.5%. 위험 수준은 보통(26.9점)입니다.',
        'NASDAQ 100는 2026-10-14 기준 횡보 · 중립 국면입니다 (추세 점수 +1/5, 4일째). 20거래일 수익률 -0.80%, '
        '60거래일 +1.20%, 20일 실현변동성 18.4%, 52주 고점 대비 -6.2%. 위험 수준은 보통(38.6점)입니다.']},
        ensure_ascii=False)

    def assertRejected(self, text, why):
        with self.assertRaisesRegex(ValueError, why):
            briefing.validate_llm({'executive_summary': text}, self.FACTS)

    def test_llm_validation_adversarial(self):
        self.assertRejected('NASDAQ 위험 26.9점, S&P 38.6점, 모멘텀 -2.21%로 상승할 것으로 보입니다', '')  # review repro
        self.assertRejected('NASDAQ 100 위험 점수는 26.9점이고, S&P 500 위험 점수는 38.6점입니다.', '다른 시장')
        self.assertRejected('나스닥의 20거래일 수익률은 +2.21%로 집계되었습니다.', '다른 시장')
        self.assertRejected('S&P 500의 20거래일 수익률은 -2.21%로 집계되었습니다.', '부호')
        self.assertRejected('NASDAQ 100의 20거래일 수익률은 0.80%로 집계되었습니다.', '부호')
        self.assertRejected('S&P 500 위험 점수는 26.9점이며 앞으로 상승할 가능성이 큽니다.', '전망·권유')
        self.assertRejected('S&P 500 위험 점수는 26.9점으로 상승 전망이 우세합니다.', '전망·권유')
        self.assertRejected('S&P 500은 26.9점으로 지금이 매수 기회입니다.', '전망·권유')
        self.assertRejected('NASDAQ 100의 위험 점수는 S&P 500의 두 배 수준에 가깝습니다.', '배수')
        self.assertRejected('S&P 500 위험 점수는 27.5점으로 보통 수준입니다.', '없는 수치')

    def test_llm_validation_accepts_faithful_rewrite(self):
        text = ('S&P 500는 상승 추세(추세 점수 +3/5)로 20거래일 수익률 +2.21%, 위험 점수 26.9점입니다. '
                'NASDAQ 100는 횡보 국면이며 20거래일 수익률 -0.80%, 52주 고점 대비 -6.2%, 위험 점수 38.6점입니다. '
                '두 시장 모두 위험 수준은 보통이며, 국면 분류는 현재 상태의 서술입니다.')
        self.assertEqual(briefing.validate_llm({'executive_summary': text}, self.FACTS)['executive_summary'], text)

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

        class Spy(Fake):
            def create(self, **kw):
                self.seen = kw['messages'][0]['content']
                return super().create(**kw)
        spy, facts = Spy(json.dumps({'executive_summary': good_text}, ensure_ascii=False)), json.loads(json.dumps(b))
        facts['sections']['us_overview'] = ['REDACTED VIEW']
        briefing.llm_rewrite(b, spy, facts_brief=facts)
        self.assertIn('REDACTED VIEW', spy.seen)   # the model sees the restricted copy, not the display copy
        self.assertNotIn(b['sections']['us_overview'][0], spy.seen)


class DailyTest(unittest.TestCase):
    """v0.4 day-over-day changes: thresholds fixed in mi.daily (2026-10-15)."""

    def state(self, regime_='Bullish / Low Volatility', raw=None, trend=4, score=30.0, level=('Moderate', '보통'),
              comps=None, vol=0.5):
        return {'as_of': '2026-10-14', 'regime': regime_, 'raw_regime': raw or regime_, 'trend_score': trend,
                'vol20_pct': vol, 'risk': {'score': score, 'level': level[0], 'level_ko': level[1],
                                           'components': comps or {'realized_vol': .3, 'drawdown': .1, 'rates': .5}}}

    def kinds(self, prev, cur, **kw):
        return [c['kind'] for c in daily.diff(prev, cur, **kw)]

    def test_no_change_and_threshold_edges(self):
        self.assertEqual(daily.diff(self.state(), self.state()), [])
        self.assertEqual(self.kinds(self.state(score=30.0), self.state(score=39.9)), [])
        self.assertEqual(self.kinds(self.state(score=30.0), self.state(score=40.0)), ['risk_score'])
        self.assertEqual(self.kinds(self.state(trend=4), self.state(trend=3)), [])
        self.assertEqual(self.kinds(self.state(trend=4), self.state(trend=2)), ['trend'])
        self.assertEqual(self.kinds(self.state(vol=0.69), self.state(vol=0.70)), ['volatility'])
        self.assertEqual(daily.diff(None, self.state()), [])

    def test_regime_band_and_pending_signal(self):
        prev = self.state()
        cur = self.state(regime_='Sideways / Neutral', level=('Elevated', '높음'), score=31)
        self.assertEqual(self.kinds(prev, cur), ['regime', 'risk_band'])
        pending = self.state(raw='Bearish / Low Volatility')
        got = daily.diff(prev, pending, pending_days=1, confirm_days=3)
        self.assertEqual([c['kind'] for c in got], ['signal'])
        self.assertIn('전환 대기 1/3일', got[0]['text'])

    def test_component_set_change_is_not_compared_as_a_score_move(self):
        prev = self.state(score=30.0)
        cur = self.state(score=60.0, comps={'realized_vol': .3, 'drawdown': .1, 'rates': .5, 'vix': .9})
        got = daily.diff(prev, cur)
        self.assertEqual([c['kind'] for c in got], ['risk_components'])
        self.assertIn('추가: VIX', got[0]['text'])

    def test_plain_text_has_no_price_derived_numbers(self):
        prev, cur = self.state(score=20.0, trend=5, vol=0.5), self.state(score=45.5, trend=1, vol=0.8)
        for c in daily.diff(prev, cur):
            self.assertNotIn('45.5', c['plain'])
            self.assertNotIn('0.80', c['plain'])
            self.assertNotIn('+1', c['plain'])

    def test_analyze_previous_state_is_point_in_time(self):
        d = bdays(320)
        close = walk(320, 5)
        r = regime.analyze('sp', rows(d, close), {})
        cut = regime.analyze('sp', rows(d[:-1], close[:-1]), {})
        prev = r['previous']
        self.assertEqual(prev['as_of'], cut['as_of'])            # yesterday's state, as computed yesterday
        self.assertEqual(prev['regime'], cut['regime'])
        self.assertEqual(prev['risk']['score'], cut['risk']['score'])
        self.assertEqual(prev['trend_score'], cut['trend_score'])
        self.assertEqual(len(r['trend_rules']), 5)
        self.assertTrue(all(isinstance(t['value'], float) for t in r['trend_rules']))
        self.assertGreaterEqual(r['pending_days'], 0)
        block = daily.market_block(r)
        self.assertEqual(block['previous_as_of'], cut['as_of'])
        self.assertTrue(block['explanation'][0].startswith('추세 점수'))
        self.assertIsNone(daily.market_block({'status': 'unavailable'}))

    def test_pending_days(self):
        B, S = 'Bullish / Low Volatility', 'Sideways / Neutral'
        self.assertEqual(regime._pending_days([B, B, S, S], B), 2)
        self.assertEqual(regime._pending_days([B, S, B], B), 0)
        self.assertEqual(regime._pending_days([], B), 0)

    def test_macro_changes(self):
        h = lambda a, b: {'history': [{'date': '2026-10-08', 'value': a}, {'date': '2026-10-09', 'value': b}]}
        self.assertEqual(daily.macro_changes({'UST10Y': h(4.00, 4.09), 'USDKRW': h(1300, 1312)}), [])
        got = daily.macro_changes({'UST10Y': h(4.00, 4.10), 'USDKRW': h(1300, 1313)})
        self.assertEqual(len(got), 2)
        self.assertIn('+10bp', got[0])
        self.assertIn('+1.00%', got[1])

    def test_snapshot_idempotent_revision_appended_and_restricted_payload(self):
        r = regime.analyze('ks', rows(bdays(320), walk(320, 6)), {})
        r.update(source_id='krx_openapi', daily=daily.market_block(r))
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'mi.sqlite'
            with store.connect(db) as conn:
                self.assertTrue(store.save_daily(conn, daily.snapshot('ks', r)))
                self.assertFalse(store.save_daily(conn, daily.snapshot('ks', r)))       # same day, same payload
                revised = json.loads(json.dumps(r))
                revised['risk']['score'] = (r['risk']['score'] or 0) + 1
                self.assertTrue(store.save_daily(conn, daily.snapshot('ks', revised)))  # revision kept as a new row
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM daily_snapshots').fetchone()[0], 2)
        kept = json.loads(daily.snapshot('sp', dict(r, source_id='fred_sp500'), restricted=True)['payload'])
        self.assertEqual(set(kept), {'regime', 'raw_regime', 'risk_level', 'components_available', 'changes'})
        full = json.loads(daily.snapshot('ks', r)['payload'])
        self.assertIn('risk_score', full)

    def test_briefing_uses_daily_changes_and_summary(self):
        r = regime.analyze('sp', rows(bdays(320), walk(320, 3)), {})
        r['daily'] = {'previous_as_of': '2026-10-13', 'changes': [daily._change('risk_score', '위험 점수 20.0 → 31.0 (+11.0)',
                                                                                '위험 점수 10점 이상 상승')],
                      'explanation': []}
        shown = briefing.build({'sp': r}, {}, {}, [], {'items': []}, macro_changes=['미국 10년물 금리 전 관측일 대비 +12bp'])
        kept = briefing.build({'sp': r}, {}, {}, [], {'items': []}, restricted={'sp'}, macro_changes=[])
        self.assertIn('S&P 500 위험 점수 20.0 → 31.0 (+11.0)', shown['sections']['outlook_changes'])
        self.assertIn('S&P 500 위험 점수 10점 이상 상승', kept['sections']['outlook_changes'])
        self.assertIn('변화 1건', shown['sections']['daily_summary'][0])
        self.assertIn('+12bp', shown['sections']['daily_summary'][-1])
        self.assertNotIn('점 ·', kept['sections']['daily_summary'][0])   # no risk score for a restricted market
        self.assertIn('기준 이상 변화 없음', kept['sections']['daily_summary'][-1])


class StorageTest(unittest.TestCase):
    def test_empty_new_archive_is_not_backed_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            with store.connect(Path(tmp) / 'mi.sqlite'):
                pass
            self.assertFalse((Path(tmp) / 'backups').exists())

    def test_migration_of_existing_archive_backs_up_first_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'mi.sqlite'
            import sqlite3
            with sqlite3.connect(db) as raw:      # an unversioned (v1) archive with data
                raw.execute("CREATE TABLE job_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL, "
                            "started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, message TEXT)")
                raw.execute("INSERT INTO job_runs (job, started_at, status) VALUES ('ingest', 'x', 'ok')")
            raw.close()
            with store.connect(db) as conn:
                self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], store.SCHEMA_VERSION)
                rep = store.storage_report(conn)
            backups = list((Path(tmp) / 'backups').glob('mi-*-pre-v%d.sqlite' % store.SCHEMA_VERSION))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(rep['integrity'], 'ok')
            self.assertIn('daily_snapshots', rep['rows'])
            self.assertEqual(rep['backups'], 1)
            with store.connect(db) as conn:                      # already current: no second backup
                store.backup(conn)
            self.assertEqual(len(list((Path(tmp) / 'backups').glob('*.sqlite'))), 2)


class BackfillTest(unittest.TestCase):
    NOW = datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc)      # 12:00 KST Thursday

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'mi.sqlite'
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, holidays=(), fail_on=None):
        def f(cfg, d):
            self.calls.append((cfg['symbol'], d))
            if d == fail_on:
                raise RuntimeError('데이터 요청 실패 (HTTP 401) · 인증키 유효성 확인')
            return None if d in holidays else {'date': d.isoformat(), 'close': 2500.0 + d.toordinal() % 97}
        return f

    def run_(self, conn, **kw):
        kw.setdefault('fetch', self.fetch())
        return backfill.run(conn, kw.pop('start', date(2026, 9, 28)), kw.pop('markets', ('ks',)), sleep=lambda s: None,
                            now=self.NOW, log=lambda *a: None, **kw)

    def test_weekdays_up_to_yesterday_and_resume(self):
        with store.connect(self.db) as conn:
            rep = self.run_(conn, fetch=self.fetch(holidays={date(2026, 10, 3), date(2026, 10, 9)}))
            days = [d for _, d in self.calls]
            self.assertTrue(all(d.weekday() < 5 for d in days))
            self.assertEqual(max(days), date(2026, 10, 14))              # never today: published next morning
            self.assertEqual(rep['markets']['ks']['no_data'], 1)          # 10-09 Hangul Day (10-03 is a Saturday)
            self.assertEqual(rep['markets']['ks']['remaining'], 1)        # recent no-data day is asked again later
            self.calls.clear()
            again = self.run_(conn)
            self.assertEqual([d for _, d in self.calls], [date(2026, 10, 9)])
            self.assertEqual(again['markets']['ks']['stored'], 1)
            self.assertEqual(backfill.coverage(conn, 'ks')['rows'], 13)

    def test_old_holiday_is_not_asked_again(self):
        with store.connect(self.db) as conn:
            self.run_(conn, start=date(2026, 9, 1), fetch=self.fetch(holidays={date(2026, 9, 24)}))
            self.calls.clear()
            self.run_(conn, start=date(2026, 9, 1))
            self.assertEqual(self.calls, [])
            cal = dict(conn.execute("SELECT date, status FROM krx_calendar WHERE market='ks'").fetchall())
            self.assertEqual(cal['2026-09-24'], 'no_data')

    def test_daily_budget_is_shared_across_runs(self):
        with store.connect(self.db) as conn:
            rep = self.run_(conn, max_calls=5)
            self.assertEqual(rep['calls'], 5)
            self.assertIn('budget', rep['stopped'])
            with patch.object(backfill, 'DAILY_BUDGET', 7):
                rep = self.run_(conn)
            self.assertEqual(rep['calls'], 2)                              # 5 used earlier today
            self.assertEqual(backfill.calls_today(conn, backfill.kst_today(self.NOW)), 7)

    def test_provider_error_stops_and_keeps_progress(self):
        with store.connect(self.db) as conn:
            rep = self.run_(conn, markets=('ks', 'kq'), fetch=self.fetch(fail_on=date(2026, 10, 1)))
            self.assertIn('HTTP 401', rep['stopped'])
            self.assertEqual(backfill.coverage(conn, 'ks')['rows'], 3)    # 09-28..09-30 kept
            self.assertNotIn('kq', rep['markets'])                        # stopped before the next market
        self.assertNotIn('secret', rep['stopped'])


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
