"""Offline regression checks: formulas, validation, source parsing, last-good writes."""
import contextlib
import copy
from datetime import date, timedelta
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import collect_data as collector
import providers
import collect_macro as macro


class IndicatorsTest(unittest.TestCase):
    def rows(self, values):
        return [{'date': (date(2025, 1, 1)+timedelta(days=i)).isoformat(), 'close': c} for i,c in enumerate(values)]

    def test_known_wilder_rsi(self):
        prices=[44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,45.84,46.08,45.89,46.03,45.61,46.28,46.28]
        self.assertAlmostEqual(collector.indicators(self.rows(prices))['rsi14'],70.464135,places=5)

    def test_linear_series(self):
        v=collector.indicators(self.rows(list(range(1,251))))
        self.assertEqual(v['sma20'],240.5)
        self.assertEqual(v['sma50'],225.5)
        self.assertEqual(v['sma200'],150.5)
        self.assertEqual(v['rsi14'],100)
        self.assertAlmostEqual(v['macd'],7)
        self.assertAlmostEqual(v['macd_signal'],7)
        self.assertAlmostEqual(v['macd_histogram'],0)
        self.assertAlmostEqual(v['return20'],(250/230-1)*100)

    def test_flat_and_insufficient_history(self):
        v=collector.indicators(self.rows([100]*250))
        self.assertEqual(v['rsi14'],50)
        self.assertEqual(v['realized_volatility20'],0)
        v=collector.indicators(self.rows([100,101]))
        self.assertIsNone(v['rsi14'])
        self.assertIsNone(v['sma200'])
        self.assertIsNone(v['macd'])
        self.assertIsNone(v['return20'])

    def test_volatility(self):
        prices=[100*math.exp(i*.01 + .02*(i%2)) for i in range(21)]
        # 10 log returns +.03, 10 returns -.01; sample sd sqrt(20*.02^2/19).
        expected=math.sqrt(20*.02**2/19)*math.sqrt(252)*100
        self.assertAlmostEqual(collector.indicators(self.rows(prices))['realized_volatility20'],expected)

    def test_bad_rows(self):
        for rows in [[{'date':'2026-01-01','close':float('nan')},{'date':'2026-01-02','close':5}],
                     [{'date':'2026-01-01','close':5},{'date':'2026-01-01','close':5}],
                     [{'date':'2026-01-01','close':5},{'date':'2027-01-01','close':5}],
                     [{'date':'2026-01-01','close':-1},{'date':'2026-01-02','close':5}]]:
            with self.assertRaises(ValueError): collector.validate_rows(rows,date(2026,1,3))

    def test_fred_missing_and_html(self):
        with patch.object(providers,'request_text',return_value='observation_date,SP500\n2026-01-01,.\n2026-01-02,100\n2026-01-05,101\n'):
            rows=providers.fred(providers.MARKETS['sp'],[],date(2026,1,6))
            self.assertEqual([r['close'] for r in rows],[100,101])
        with patch.object(providers,'request_text',return_value='<html>blocked</html>'):
            with self.assertRaises(ValueError): providers.fred(providers.MARKETS['sp'],[],date(2026,1,6))

    def test_krx_parser_and_missing_key(self):
        with patch.dict('os.environ',{},clear=True):
            with self.assertRaisesRegex(RuntimeError,'KRX_API_KEY'): providers.krx(providers.MARKETS['ks'],[],date(2026,1,5))
        reply={'OutBlock_1':[{'IDX_NM':'코스피 200','BAS_DD':'20260105','CLSPRC_IDX':'999'},
                             {'IDX_NM':'코스피','BAS_DD':'20260105','CLSPRC_IDX':'2,600.50'}]}
        previous=[{'date':'2026-01-05','close':2600}]
        with patch.dict('os.environ',{'KRX_API_KEY':'offline-test'}),patch.object(providers,'request_text',side_effect=lambda url,headers,json_body: json.dumps(reply if json_body['basDd']=='20260105' else {'OutBlock_1':[]})):
            rows=providers.krx(providers.MARKETS['ks'],previous,date(2026,1,5))
            self.assertEqual(rows[-1]['close'],2600.5)

    def test_http_diagnostics_and_key_privacy(self):
        from subprocess import CompletedProcess
        marker = '\n__MARKET_HTTP_STATUS__:'
        for code, status, message in [(56, '403', 'HTTP 403'), (6, '000', 'DNS'),
                                       (28, '000', '시간 초과'), (0, '429', '요청 한도')]:
            result = CompletedProcess([], code, 'private response'+marker+status, 'private stderr')
            with patch.object(providers.subprocess, 'run', return_value=result), patch.object(providers.time, 'sleep'):
                with self.assertRaisesRegex(RuntimeError, message) as caught:
                    providers.request_text('https://example.test', {'AUTH_KEY':'secret-test-key'}, {'basDd':'20261014'})
                self.assertNotIn('secret-test-key', str(caught.exception))
                self.assertNotIn('private response', str(caught.exception))
        with patch.object(providers.subprocess, 'run', return_value=CompletedProcess([],0,'{}'+marker+'200','')) as run:
            self.assertEqual(providers.request_text('https://example.test',{'AUTH_KEY':'secret-test-key'},{'basDd':'20261014'}),'{}')
            args=run.call_args.args[0]
            self.assertIn('POST',args)
            self.assertNotIn('secret-test-key',' '.join(args))
            self.assertNotIn('--location',args)
            self.assertIn('AUTH_KEY: secret-test-key',run.call_args.kwargs['input'])

    def test_timeout_retry_and_auth_no_retry(self):
        from subprocess import CompletedProcess
        marker='\n__MARKET_HTTP_STATUS__:'
        timeout=CompletedProcess([],28,marker+'000','')
        ok=CompletedProcess([],0,'{}'+marker+'200','')
        with patch.object(providers.subprocess,'run',side_effect=[timeout,ok]) as run, patch.object(providers.time,'sleep') as sleep:
            self.assertEqual(providers.request_text('https://example.test'),'{}')
            self.assertEqual(run.call_count,2)
            sleep.assert_called_once_with(1)
        for status in ('401','403','429'):
            with patch.object(providers.subprocess,'run',return_value=CompletedProcess([],0,marker+status,'')) as run, patch.object(providers.time,'sleep') as sleep:
                with self.assertRaises(RuntimeError): providers.request_text('https://example.test')
                self.assertEqual(run.call_count,1)
                sleep.assert_not_called()
        with patch.object(providers.subprocess,'run',return_value=timeout) as run, patch.object(providers.time,'sleep'):
            with self.assertRaises(RuntimeError): providers.request_text('https://example.test')
            self.assertEqual(run.call_count,3)

    def test_selected_market_preserves_other_snapshots(self):
        today=date(2026,10,15)
        rows=self.rows(list(range(100,350)))
        # Align the end of the fixture with the collection date.
        for i,row in enumerate(rows): row['date']=(today-timedelta(days=249-i)).isoformat()
        def ok(*a): return copy.deepcopy(rows)
        def must_not_call(*a): raise AssertionError('Unselected provider was called')
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            path=Path(directory)/'markets.json'
            collector.collect(path,{'fred':ok,'krx':ok},today)
            before=json.loads(path.read_text())
            calls=[]
            def one(config,previous,day):
                calls.append(config['symbol'])
                return copy.deepcopy(rows)
            self.assertEqual(collector.collect(path,{'fred':must_not_call,'krx':one},today,selected=['ks']),0)
            self.assertEqual(calls,['KOSPI'])
            after=json.loads(path.read_text())
            for key in ('sp','nq','kq'): self.assertEqual(before['markets'][key],after['markets'][key])

    def test_last_good_atomic_snapshot(self):
        today=date(2026,10,15)
        rows=[{'date':(today-timedelta(days=249-i)).isoformat(),'close':100+i} for i in range(250)]
        def ok(*args): return copy.deepcopy(rows)
        def fail(*args): raise RuntimeError('offline provider failure')
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            path=Path(directory)/'markets.json'
            self.assertEqual(collector.collect(path,{'fred':ok,'krx':ok},today),0)
            first=json.loads(path.read_text())
            self.assertEqual(collector.collect(path,{'fred':fail,'krx':fail},today),4)
            second=json.loads(path.read_text())
            self.assertEqual(first['last_success_at'],second['last_success_at'])
            for key in first['markets']:
                for field in ('history','value','indicators','as_of','updated_at'):
                    self.assertEqual(first['markets'][key][field],second['markets'][key][field])
                self.assertEqual(second['markets'][key]['status'],'stale')
            # A failed source must not prevent other markets updating.
            self.assertEqual(collector.collect(path,{'fred':ok,'krx':fail},today),2)
            third=json.loads(path.read_text())
            self.assertEqual(third['markets']['sp']['status'],'ok')
            self.assertEqual(third['markets']['ks']['status'],'stale')
            # Partial/older payload must preserve history.
            self.assertEqual(collector.collect(path,{'fred':lambda *a:rows[-2:],'krx':fail},today),4)
            self.assertEqual(json.loads(path.read_text())['markets']['sp']['history'],rows)
            path.write_text('{broken')
            with self.assertRaises(json.JSONDecodeError): collector.collect(path,{'fred':ok,'krx':ok},today)
            self.assertEqual(path.read_text(),'{broken')


class V03Test(unittest.TestCase):
    def test_extended_metrics_and_year_boundary(self):
        today=date(2026,10,15)
        rows=[{'date':(today-timedelta(days=399-i)).isoformat(),'close':100+i} for i in range(400)]
        m=collector.indicators(rows)
        self.assertAlmostEqual(m['return5'],(499/494-1)*100)
        self.assertAlmostEqual(m['return60'],(499/439-1)*100)
        self.assertEqual(m['drawdown52w'],0)
        self.assertEqual(m['high52w'],499)
        self.assertAlmostEqual(m['distance_sma20'],(499/m['sma20']-1)*100)
        self.assertEqual(collector.market_regime(499,m)['code'],'uptrend')
        self.assertIsNone(collector.indicators(rows[-100:])['drawdown52w'])
        # An old extreme outside 52 weeks must not affect the current window high.
        rows[0]['close']=99999
        self.assertEqual(collector.indicators(rows)['high52w'],499)
        rows[-1]['close']=400
        self.assertAlmostEqual(collector.indicators(rows)['drawdown52w'],(400/498-1)*100)

    def test_regime_down_mixed_and_insufficient(self):
        m={'sma20':110,'sma50':120,'sma200':130,'return20':-5,'realized_volatility20':26}
        self.assertEqual(collector.market_regime(100,m)['code'],'downtrend')
        self.assertEqual(collector.market_regime(100,m)['volatility'],'높음')
        self.assertEqual(collector.market_regime(125,m)['code'],'mixed')
        self.assertEqual(collector.market_regime(100,{})['code'],'unavailable')

    def test_macro_signed_values_duplicates_and_missing(self):
        config=macro.MACRO['yield_spread']
        rows=[{'date':'2026-10-13','close':-0.1},{'date':'2026-10-14','close':0}]
        self.assertEqual(macro.validate_macro_rows(rows,config,date(2026,10,15)),rows)
        with self.assertRaises(ValueError):macro.validate_macro_rows(rows+rows[-1:],config,date(2026,10,15))
        with self.assertRaises(ValueError):macro.validate_macro_rows([{'date':'2026-10-14','close':float('inf')}],config,date(2026,10,15))
        with self.assertRaises(ValueError):macro.validate_macro_rows([{'date':'2026-10-14','close':-1}],macro.MACRO['vix'],date(2026,10,15))

    def test_macro_last_good_and_release_time_separation(self):
        today=date(2026,10,15)
        def ok(*a):return [{'date':'2026-10-13','close':3},{'date':'2026-10-14','close':4}]
        def fail(*a):raise RuntimeError('offline')
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            path=Path(directory)/'macro.json'
            self.assertEqual(macro.collect_macro(path,{'fred':ok,'ecos':ok},today),1) # DXY deliberately absent
            before=json.loads(path.read_text())
            m=before['indicators']['us2y']
            self.assertIsNone(m['publication_date'])
            self.assertIsNone(m['source_updated_at'])
            self.assertNotEqual(m['observation_date'],m['updated_at'])
            self.assertFalse(m['usage']['ml_training_allowed'])
            self.assertEqual(macro.collect_macro(path,{'fred':fail,'ecos':fail},today),10)
            after=json.loads(path.read_text())
            for field in ('value','history','observation_date','updated_at'):
                self.assertEqual(before['indicators']['us2y'][field],after['indicators']['us2y'][field])
            self.assertEqual(after['indicators']['us2y']['status'],'stale')
            self.assertEqual(after['indicators']['dxy']['status'],'unavailable')
            path.write_text('{broken')
            with self.assertRaises(json.JSONDecodeError):macro.collect_macro(path,{'fred':ok,'ecos':ok},today)
            self.assertEqual(path.read_text(),'{broken')

    def test_ecos_parser_and_key_not_in_process_arguments(self):
        config=macro.MACRO['bok_rate']
        reply={'StatisticSearch':{'list_total_count':1,'row':[{'STAT_CODE':'722Y001','ITEM_CODE1':'0101000','TIME':'20261014','DATA_VALUE':'2.50'}]}}
        with patch.dict('os.environ',{'ECOS_API_KEY':'test-ecos-secret'}),patch.object(macro,'request_text',return_value=json.dumps(reply)):
            self.assertEqual(macro.ecos(config,[],date(2026,10,15))[0]['close'],2.5)
        from subprocess import CompletedProcess
        with patch.object(providers.subprocess,'run',return_value=CompletedProcess([],0,'{}\n__MARKET_HTTP_STATUS__:200','')) as run:
            providers.request_text('https://ecos.bok.or.kr/api/test-ecos-secret',{'Accept':'application/json'})
            self.assertNotIn('test-ecos-secret',' '.join(run.call_args.args[0]))
            self.assertIn('test-ecos-secret',run.call_args.kwargs['input'])


if __name__=='__main__': unittest.main()
