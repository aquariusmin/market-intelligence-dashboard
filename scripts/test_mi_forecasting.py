"""v0.5/v0.6 checks. Synthetic series exist only inside these tests and are never written to data/."""
import json
import math
import tempfile
import unittest
from datetime import date, time, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from mi import dataset, forecasting, licenses, models, store, timing, validation


def bdays(n, start=date(2010, 1, 4)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def walk(n, seed, drift=0.0003, vol=0.01, signal=0.0):
    """Random walk; with signal>0 tomorrow's return leans on today's 5-day return (learnable)."""
    rng = np.random.default_rng(seed)
    r = np.zeros(n)
    for t in range(1, n):
        past5 = r[max(0, t - 5):t].sum()
        r[t] = drift + signal * past5 + rng.normal(0, vol)
    return 100 * np.exp(np.cumsum(r))


def rows(dates, closes):
    return [{'date': d.isoformat(), 'close': float(c)} for d, c in zip(dates, closes)]


MANIFEST = {m: {'source_id': 'licensed:' + m, 'provider': 'test', 'dataset': 'synthetic', 'terms_url': 'test://',
                'ml_training': 'allowed', 'local_storage': 'allowed', 'verified_on': '2026-10-15',
                'publication_delay_minutes': 15, 'manifest_errors': []} for m in ('sp', 'nq', 'ks', 'kq')}
FAST_GRID = {'class_frequency': [{}], 'momentum_rule': [{}], 'logistic_regression': [{'C': 0.1}],
             'lightgbm': [{'num_leaves': 4, 'n_estimators': 60}]}


class GateTest(unittest.TestCase):
    def test_fred_and_unknown_sources_refused(self):
        for sid in ('fred_sp500', 'fred_nasdaq100', 'krx_openapi', 'fred_vixcls', 'nope'):
            self.assertFalse(licenses.ml_allowed(sid, {})[0], sid)
        self.assertTrue(licenses.ml_allowed('fed_h15', {})[0])
        s = {'sp': {'rows': rows(bdays(300), walk(300, 1)), 'source_id': 'fred_sp500'}}
        with self.assertRaises(dataset.GateError):
            dataset.build('sp', s, {}, 5, {})

    def test_incomplete_manifest_refused(self):
        bad = {'sp': dict(MANIFEST['sp'], manifest_errors=['terms_url'])}
        self.assertFalse(licenses.ml_allowed('licensed:sp', bad)[0])
        notallowed = {'sp': dict(MANIFEST['sp'], ml_training='unclear')}
        self.assertFalse(licenses.ml_allowed('licensed:sp', notallowed)[0])

    def test_unlicensed_inputs_dropped_from_features(self):
        d = bdays(400)
        s = {'sp': {'rows': rows(d, walk(400, 1)), 'source_id': 'licensed:sp'},
             'nq': {'rows': rows(d, walk(400, 2)), 'source_id': 'fred_nasdaq100'}}
        macro = {'VIX': [(x.isoformat(), 20.0) for x in d], 'UST10Y': [(x.isoformat(), 4.0) for x in d]}
        ds = dataset.build('sp', s, macro, 5, MANIFEST)
        self.assertNotIn('vix', ds['X'].columns)
        self.assertFalse(any(c.startswith('nq_') for c in ds['X'].columns))
        self.assertIn('ust10y', ds['X'].columns)
        self.assertTrue(all(licenses.ml_allowed(sid, MANIFEST)[0] for sid in ds['feature_sources'].values()))


class LeakageTest(unittest.TestCase):
    def test_features_do_not_change_when_future_changes(self):
        d = bdays(500)
        a = walk(500, 3)
        b = a.copy()
        b[400:] *= np.exp(np.linspace(0, .5, 100))  # rewrite the future only
        mk = lambda c: {'sp': {'rows': rows(d, c), 'source_id': 'licensed:sp'},
                        'ks': {'rows': rows(d, walk(500, 4)), 'source_id': 'licensed:ks'}}
        xa = dataset.build('sp', mk(a), {}, 5, MANIFEST)['X']
        xb = dataset.build('sp', mk(b), {}, 5, MANIFEST)['X']
        pd.testing.assert_frame_equal(xa.iloc[:400], xb.iloc[:400])

    def test_korea_sees_previous_us_close_only(self):
        d = bdays(80, date(2026, 3, 2))   # spans the US DST switch (2026-03-08)
        us = np.arange(1, 81, dtype=float) * 10 + 1000
        s = {'ks': {'rows': rows(d, walk(80, 5)), 'source_id': 'licensed:ks'},
             'sp': {'rows': rows(d, us), 'source_id': 'licensed:sp'}}
        X = dataset.build('ks', s, {}, 1, MANIFEST)['X']
        # sp_ret1 at KR date t must equal the US return of t-1 (the US close of t is still in the future)
        us_ret = np.log(pd.Series(us)).diff() * 100
        for t in range(3, 80):
            self.assertAlmostEqual(X['sp_ret1'].iloc[t], us_ret.iloc[t - 1], places=9)
        # and the US sees the same-day Korean close (closed ~14h earlier)
        Xu = dataset.build('sp', s, {}, 1, MANIFEST)['X']
        kr = pd.Series([r['close'] for r in s['ks']['rows']])
        self.assertAlmostEqual(Xu['ks_ret1'].iloc[30], (math.log(kr[30]) - math.log(kr[29])) * 100, places=9)

    def test_macro_publication_lag(self):
        # H.15 value for Friday becomes usable only after the next Fed business day 17:00 ET.
        fri = date(2026, 10, 2)
        avail = timing.macro_available_at('next_business_day_17et', [fri])[0]
        self.assertEqual(avail.tz_convert('America/New_York').date(), date(2026, 10, 5))
        self.assertGreater(avail, timing.decision_time('sp', fri))
        h10 = timing.macro_available_at('weekly_h10', [fri])[0].tz_convert('America/New_York')
        self.assertEqual(h10.date(), date(2026, 10, 6))
        # Monday 2026-10-12 is Columbus Day: Friday 10-09 H.15 is posted Tuesday, so the KOSPI decision on
        # Tuesday (16:00 KST = 03:00 ET) must not see it yet.
        avail = timing.macro_available_at('next_business_day_17et', [date(2026, 10, 9)])[0]
        self.assertEqual(avail.tz_convert('America/New_York').date(), date(2026, 10, 13))
        self.assertGreater(avail, timing.decision_time('ks', date(2026, 10, 13)))
        # Independence Day on a Saturday (2026-07-04) is observed Friday 07-03 -> Thursday's value is posted Monday
        self.assertEqual(timing.macro_available_at('next_business_day_17et', [date(2026, 7, 2)])[0]
                         .tz_convert('America/New_York').date(), date(2026, 7, 6))

    def test_asof_ties_pick_latest_observation(self):
        # A whole H.10 week shares one release time: the join must use the latest day, not an arbitrary one.
        week = [date(2026, 10, 5) + timedelta(days=i) for i in range(5)]
        rel = [timing._local(date(2026, 10, 13), time(16, 15), timing.ET)] * 5
        dec = pd.to_datetime([timing._local(date(2026, 10, 14), time(9, 0), timing.ET)] * 3, utc=True)
        for perm in ([0, 1, 2, 3, 4], [4, 3, 2, 1, 0], [2, 4, 0, 3, 1]):
            got = timing.asof_join(dec, [week[i] for i in perm], pd.to_datetime([rel[i] for i in perm], utc=True),
                                   np.array([float(i) for i in perm]), 'v')
            self.assertTrue((got == 4.0).all())

    def test_unstated_licensed_publication_is_next_day(self):
        # A licensed source without publication_delay_minutes: its close is known only next business day.
        m = {'ks': dict(MANIFEST['ks'])}
        m['ks'].pop('publication_delay_minutes')
        d = [date(2026, 10, 14)]
        late = timing.price_available_at('ks', d, 'licensed:ks', m)[0]
        self.assertGreater(late, timing.decision_time('sp', d[0]))
        quick = timing.price_available_at('ks', d, 'licensed:ks', MANIFEST)[0]
        self.assertLess(quick, timing.decision_time('sp', d[0]))
        self.assertGreater(timing.price_available_at('ks', d, 'krx_openapi')[0], timing.decision_time('sp', d[0]))

    def test_dst_close_times(self):
        self.assertEqual(timing.close_utc('sp', date(2026, 7, 1)).hour, 20)   # EDT
        self.assertEqual(timing.close_utc('sp', date(2026, 12, 1)).hour, 21)  # EST
        self.assertEqual(timing.close_utc('ks', date(2026, 7, 1)).hour, 6)    # 15:30 KST

    def test_labels_and_threshold_use_past_vol_only(self):
        c = pd.Series(walk(300, 6), index=pd.DatetimeIndex(pd.to_datetime(bdays(300))))
        y, fwd, thr = dataset.labels(c, 5)
        c2 = c.copy()
        c2.iloc[200:] *= 1.5
        _, _, thr2 = dataset.labels(c2, 5)
        pd.testing.assert_series_equal(thr.iloc[:200], thr2.iloc[:200])
        self.assertTrue(y.iloc[-5:].isna().all())
        ok = y.notna()
        self.assertTrue(((fwd[ok] > thr[ok]) == (y[ok] == 2)).all())


class SplitTest(unittest.TestCase):
    def test_purged_folds(self):
        n, h = 1500, 20
        outcome = np.arange(n) + h
        labelled = outcome < n
        cfg = validation.Config(horizon=h, min_train=300)
        sp = validation.spans(n, outcome, labelled, cfg)
        dev_start, dev_end, hold_start, hold_end = sp
        self.assertGreaterEqual(hold_start - dev_end, h)
        count = 0
        for fit, cal, test in validation.folds(n, outcome, labelled, cfg, hold_start, hold_end):
            s = test[0]
            self.assertTrue((outcome[fit] + cfg.embargo <= cal[0]).all())
            self.assertTrue((outcome[cal] + cfg.embargo <= s).all())
            self.assertTrue(fit.max() < cal.min() < s)
            count += 1
        self.assertGreater(count, 5)

    def test_calibrators_rows_sum_to_one(self):
        rng = np.random.default_rng(0)
        p = rng.dirichlet([2, 2, 2], 400)
        y = rng.integers(0, 3, 400)
        for name, make in models.CALIBRATORS.items():
            q = make().fit(p, y).transform(p)
            np.testing.assert_allclose(q.sum(1), 1, atol=1e-9, err_msg=name)
            self.assertTrue((q > 0).all())

    def test_metrics(self):
        p = np.array([[.8, .1, .1], [.1, .8, .1], [.1, .1, .8], [.1, .1, .8]])
        y = np.array([0, 1, 2, 0])
        m = validation.metrics(p, y)
        self.assertEqual(m['accuracy'], .75)
        self.assertAlmostEqual(m['brier'], ((.04 + .01 + .01) * 3 + (.81 + .01 + .64)) / 4)
        self.assertEqual(m['confusion_matrix'][0], [1, 0, 1])


class PipelineTest(unittest.TestCase):
    def run_eval(self, signal, seed):
        n = 1600
        d = bdays(n)
        s = {'sp': {'rows': rows(d, walk(n, seed, signal=signal)), 'source_id': 'licensed:sp'}}
        ds = dataset.build('sp', s, {}, 1, MANIFEST)
        cfg = validation.Config(horizon=1, min_train=400, step=42, bootstrap=200)
        with patch.dict(models.GRID, FAST_GRID, clear=True):
            return validation.evaluate(ds, cfg)

    def test_random_walk_is_not_validated(self):
        res = self.run_eval(0.0, 11)
        self.assertEqual(res['status'], 'ok')
        for name, r in res['models'].items():
            self.assertNotEqual(r['validation']['status'], 'validated', name)
            idx, p, _, _ = res['predictions'][name]
            np.testing.assert_allclose(p.sum(1), 1, atol=1e-9)
        dev_end = res['spans']['development'][1]
        self.assertLess(dev_end, res['spans']['holdout'][0])

    def test_learnable_signal_beats_baseline(self):
        res = self.run_eval(-0.35, 12)   # strong short-term mean reversion
        lr = res['models']['logistic_regression']
        self.assertGreater(lr['vs_class_frequency']['log_loss_improvement'], 0)
        self.assertGreater(lr['balanced_accuracy'], res['models']['class_frequency']['balanced_accuracy'])


class StoreTest(unittest.TestCase):
    def test_append_only_live_forecasts_and_outcomes(self):
        n = 1300
        d = bdays(n, date(2021, 1, 4))
        closes = walk(n, 21)
        with tempfile.TemporaryDirectory() as tmp, patch('mi.paths.DB', Path(tmp) / 'mi.sqlite'), \
                patch('mi.paths.MARKETS_JSON', Path(tmp) / 'none.json'), patch.dict(models.GRID, FAST_GRID, clear=True):
            series = {'sp': {'rows': rows(d[:-3], closes[:-3]), 'source_id': 'licensed:sp', 'status': 'ok'}}
            fake = lambda conn, m, snap, manifest: series.get(m, {'rows': [], 'source_id': 'fred_' + m})
            with patch('mi.prices.series', side_effect=fake):
                with store.connect() as conn:
                    perf = forecasting.train(conn, markets=['sp'], horizons=(1,), manifest=MANIFEST, log=lambda *a: None,
                                             cfg_overrides={'min_train': 400, 'step': 63, 'bootstrap': 100})
                    self.assertEqual(perf['markets']['sp']['1']['status'], 'ok')
                    self.assertEqual(perf['gate']['nq']['allowed'], False)
                    f1 = forecasting.forecast(conn, perf, markets=['sp'], manifest=MANIFEST, log=lambda *a: None)
                    rec = f1['markets']['sp']['1']
                    self.assertAlmostEqual(rec['bullish_probability'] + rec['neutral_probability'] +
                                           rec['bearish_probability'], 1, places=3)
                    self.assertEqual(rec['stored'], 'new')
                    again = forecasting.forecast(conn, perf, markets=['sp'], manifest=MANIFEST, log=lambda *a: None)
                    self.assertEqual(again['markets']['sp']['1']['stored'], 'already_recorded')
                    for h in ('5', '20'):
                        self.assertEqual(f1['markets']['sp'][h]['status'], 'unavailable')
                    # a different model version on the same cutoff adds a row, never replaces
                    conn.execute("UPDATE forecasts SET model_version = model_version || '-old'")
                    forecasting.forecast(conn, perf, markets=['sp'], manifest=MANIFEST, log=lambda *a: None)
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM forecasts').fetchone()[0], 2)
                    self.assertEqual(forecasting.resolve_outcomes(conn, MANIFEST), 0)   # outcome not yet observed
                    series['sp']['rows'] = rows(d, closes)                              # new close arrives
                    self.assertEqual(forecasting.resolve_outcomes(conn, MANIFEST), 2)
                    hist = forecasting.live_history(conn)
                    self.assertTrue(all(r['status'] == 'evaluated' for r in hist))
                    exp_ret = closes[-3] / closes[-4] - 1
                    self.assertAlmostEqual(hist[0]['actual_return'], exp_ret)
                    self.assertGreater(conn.execute('SELECT COUNT(*) FROM backtest_predictions').fetchone()[0], 0)
                    # the retrain duplicate on the same cutoff is kept in the table but counted once
                    summ = forecasting.live_summary(forecasting.evaluated_live(conn))
                    self.assertEqual([e['n'] for e in summ], [1])
                    # a second training run reuses the holdout fixed by the first and records the earlier verdict
                    perf2 = forecasting.train(conn, markets=['sp'], horizons=(1,), manifest=MANIFEST, log=lambda *a: None,
                                              cfg_overrides={'min_train': 400, 'step': 63, 'bootstrap': 100})
                    e1, e2 = perf['markets']['sp']['1'], perf2['markets']['sp']['1']
                    self.assertEqual(e2['spans']['holdout'][0], e1['spans']['holdout'][0])
                    self.assertEqual(e2['holdout_fixed'], e1['spans']['holdout'][0])
                    self.assertEqual(e2['previous_run']['run_id'], e1['run_id'])
                    self.assertNotEqual(e2['run_id'], e1['run_id'])

    def test_live_summary_covers_whole_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            with store.connect(Path(tmp) / 'mi.sqlite') as conn:
                base = date(2024, 1, 1)
                for i in range(500):   # more than live_history's display limit
                    conn.execute("INSERT INTO forecasts (kind, created_at, market, horizon, model_name, model_version, "
                                 "input_cutoff, decision_time_utc, p_bull, p_neutral, p_bear, threshold, base_close, "
                                 "expected_outcome_date, validation_status, status, actual_class, provenance) "
                                 "VALUES ('live','t','sp',1,'m','v1',?,'t',.5,.3,.2,.01,100,'t','validated',"
                                 "'evaluated','bullish','{}')", ((base + timedelta(days=i)).isoformat(),))
                self.assertEqual(len(forecasting.live_history(conn)), 400)
                summ = forecasting.live_summary(forecasting.evaluated_live(conn))[0]
                self.assertEqual(summ['n'], 500)
                self.assertAlmostEqual(summ['accuracy'], 1.0)
                self.assertAlmostEqual(summ['brier'], .25 + .09 + .04)
                self.assertIsNone(summ['warning'])
                self.assertGreater(summ['skill_vs_uniform'], 0)

    def test_price_revision_logged_and_job_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'mi.sqlite'
            with store.connect(db) as conn:
                self.assertEqual(store.upsert_prices(conn, 'ks', [('2026-10-14', 2600.0)], 'krx_openapi'), (1, 0))
                self.assertEqual(store.upsert_prices(conn, 'ks', [('2026-10-14', 2600.0)], 'krx_openapi'), (0, 0))
                self.assertEqual(store.upsert_prices(conn, 'ks', [('2026-10-14', 2601.5)], 'krx_openapi'), (0, 1))
                rev = conn.execute('SELECT old_value, new_value FROM observation_revisions').fetchall()
                self.assertEqual([tuple(r) for r in rev], [(2600.0, 2601.5)])
                self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
                self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], store.SCHEMA_VERSION)
                conn.execute("INSERT INTO job_runs (job, started_at, status) VALUES ('train', '2020-01-01T00:00:00+00:00', 'running')")
            with store.job('train', db):
                pass
            with store.connect(db) as conn:
                st = [r[0] for r in conn.execute("SELECT status FROM job_runs WHERE job='train' ORDER BY id")]
            self.assertEqual(st, ['interrupted', 'ok'])


if __name__ == '__main__':
    unittest.main()
