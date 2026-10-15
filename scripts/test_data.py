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

    def test_krx_placeholder_close_and_midloop_failure(self):
        blank={'OutBlock_1':[{'IDX_NM':'코스피','BAS_DD':'20260106','CLSPRC_IDX':'-'}]}
        previous=[{'date':'2026-01-05','close':2600.0}]
        with patch.dict('os.environ',{'KRX_API_KEY':'offline-test'}):
            with patch.object(providers,'request_text',side_effect=lambda url,headers,json_body: json.dumps(blank if json_body['basDd']=='20260106' else {'OutBlock_1':[]})):
                self.assertEqual(providers.krx(providers.MARKETS['ks'],previous,date(2026,1,6)),previous)
            def flaky(url,headers,json_body):
                if json_body['basDd']=='20260106': raise RuntimeError('offline network failure')
                return json.dumps({'OutBlock_1':[]})
            with patch.object(providers,'request_text',side_effect=flaky):
                with self.assertRaisesRegex(RuntimeError,'2026-01-06'): providers.krx(providers.MARKETS['ks'],previous,date(2026,1,6))
        self.assertEqual(previous,[{'date':'2026-01-05','close':2600.0}])  # caller's last good rows untouched

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
            self.assertEqual(args[args.index('--proto')+1],'=https')
            self.assertEqual(args[args.index('--proto-redir')+1],'=https')
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


if __name__=='__main__': unittest.main()
