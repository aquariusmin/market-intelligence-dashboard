"""mi_cli orchestration tests: stage isolation, exit codes, MI_DATA_DIR isolation. Offline (no network)."""
import contextlib
import io
import json
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import mi_cli
from mi import paths


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.patches = [patch.object(paths, 'DATA', data), patch.object(paths, 'DB', data / 'mi.sqlite'),
                        patch.object(paths, 'MARKETS_JSON', data / 'markets.json'),
                        patch.object(paths, 'DASHBOARD', data / 'dashboard')]
        for p in self.patches:
            p.start()
        paths.DASHBOARD.mkdir(parents=True)
        self.quiet = contextlib.ExitStack()
        self.quiet.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.err = self.quiet.enter_context(contextlib.redirect_stderr(io.StringIO()))

    def tearDown(self):
        self.quiet.close()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def args(self, **kw):
        base = dict(markets=None, skip_prices=False, with_fred_risk=False, no_gdelt=True, llm=False, session='morning',
                    bootstrap=None)
        base.update(kw)
        return Namespace(**base)

    def test_ingest_writes_collector_output_inside_data_dir_and_isolates_archive_errors(self):
        calls = []
        run = lambda cmd: (calls.append(cmd), type('R', (), {'returncode': 0})())[1]
        with patch('mi_cli.subprocess.run', side_effect=run), \
                patch('mi.prices.ingest_snapshot', side_effect=ValueError('markets.json 스키마 오류')), \
                patch('mi.macro.collect', return_value=({'UST10Y': {'status': 'ok'}}, {})) as macro_collect:
            mi_cli.cmd_ingest(self.args())
        cmd = calls[0]
        self.assertEqual(cmd[cmd.index('--output') + 1], str(paths.MARKETS_JSON))
        macro_collect.assert_called_once()   # a broken snapshot does not stop the macro step
        ingest = json.loads((paths.DASHBOARD / 'ingest.json').read_text())
        self.assertIn('스키마 오류', ingest['archive_error'])

    def test_failed_stage_does_not_stop_others_and_sets_exit_code(self):
        ran = []
        boom = lambda a: (_ for _ in ()).throw(RuntimeError('boom'))
        cmds = dict(mi_cli.COMMANDS, regime=boom, briefing=lambda a: ran.append(list(a.failed_stages)),
                    status=lambda a: ran.append('status'))
        with patch.dict(mi_cli.COMMANDS, cmds):
            code = mi_cli.run_all(['regime', 'briefing'], self.args())
        self.assertEqual(code, 1)
        self.assertEqual(ran, [['regime'], 'status'])   # briefing ran and knew regime had failed
        self.assertIn('Traceback', self.err.getvalue())

    def test_status_failure_is_isolated_and_reported(self):
        cmds = dict(mi_cli.COMMANDS, regime=lambda a: None,
                    status=lambda a: (_ for _ in ()).throw(ValueError('status boom')))
        with patch.dict(mi_cli.COMMANDS, cmds):
            self.assertEqual(mi_cli.run_all(['regime'], self.args()), 1)
            self.assertEqual(mi_cli.main(['regime']), 1)

    def test_status_reports_broken_snapshot(self):
        paths.MARKETS_JSON.write_text('{"schema_version": 9}')
        mi_cli.cmd_status(self.args())
        status = json.loads((paths.DASHBOARD / 'status.json').read_text())
        self.assertIn('스키마', status['snapshot_error'])

    def test_fred_risk_snapshot_expires(self):
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec='seconds')
        mi_cli.write('fred_risk.json', {'fetched_at': old, 'series': {'VIX': [['2026-10-01', 20.0]]}})
        self.assertEqual(mi_cli.transient_risk(), {})
        self.assertFalse((paths.DASHBOARD / 'fred_risk.json').exists())
        fresh = datetime.now(timezone.utc).isoformat(timespec='seconds')
        mi_cli.write('fred_risk.json', {'fetched_at': fresh, 'series': {'VIX': [['2026-10-01', 20.0]]}})
        self.assertEqual(mi_cli.transient_risk(), {'VIX': [('2026-10-01', 20.0)]})

    def test_input_warnings(self):
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        mi_cli.write('regime.json', {'generated_at': '2026-01-01T00:00:00+00:00'})
        mi_cli.write('forecast.json', {'generated_at': now})
        w = mi_cli.input_warnings(['regime'])
        self.assertIn('이번 실행에서 regime 단계 실패 · 이전 결과 사용', w)
        self.assertTrue(any(x.startswith('regime.json이 오래됨') for x in w))
        self.assertIn('news.json 없음', w)
        self.assertFalse(any(x.startswith('forecast.json') for x in w))

    def test_briefing_archive_and_llm_input_exclude_fred_derived_numbers(self):
        from mi import regime as reg_mod
        import numpy as np
        import pandas as pd
        days = pd.bdate_range('2025-06-02', periods=320)
        close = 100 * np.exp(np.cumsum(np.random.default_rng(3).normal(0, .01, 320)))
        r = reg_mod.analyze('sp', [{'date': d.date().isoformat(), 'close': float(c)} for d, c in zip(days, close)], {})
        r['source_id'] = 'fred_sp500'
        mi_cli.write('regime.json', {'markets': {'sp': r}})
        with patch('mi.events.upcoming', return_value={'status': 'ok', 'next_fomc': None, 'items': []}):
            mi_cli.cmd_briefing(self.args(session='evening'))
        shown = json.loads((paths.DASHBOARD / 'briefing.json').read_text())
        from mi import store
        with store.connect() as conn:
            kept = json.loads(conn.execute('SELECT content FROM briefings').fetchone()[0])
        self.assertIn('20거래일 수익률', shown['sections']['us_overview'][0])   # screen unchanged
        self.assertNotIn('%', kept['sections']['us_overview'][0])
        self.assertEqual(kept['redacted_markets'], ['sp'])
        self.assertEqual(mi_cli.restricted_markets({'sp': r, 'ks': {'source_id': 'krx_openapi'}}), {'sp'})

    def test_sqlite_archive_is_owner_only(self):
        from mi import store
        paths.DB.touch(mode=0o644)
        with store.connect():
            pass
        self.assertEqual(paths.DB.stat().st_mode & 0o777, 0o600)

    def test_local_server_allowlist_and_host_guard(self):
        import serve
        self.assertEqual(serve.allowed_path('/'), '/index.html')
        self.assertEqual(serve.allowed_path('/data/dashboard/regime.json?x=1'), '/data/dashboard/regime.json')
        for bad in ('/data/mi.sqlite', '/.git/config', '/scripts/mi_cli.py', '/data/dashboard/../mi.sqlite',
                    '/.venv/bin/python', '/data/cron.log', '/%2e%2e/etc/passwd'):
            self.assertIsNone(serve.allowed_path(bad), bad)
        self.assertTrue(serve.allowed_host('localhost:3000'))
        self.assertTrue(serve.allowed_host('127.0.0.1'))
        for bad in ('evil.example:3000', 'localhost.evil.example', '', None, '[::1]:3000'):
            self.assertFalse(serve.allowed_host(bad), bad)

    def test_env_file_loads_only_known_keys_and_requires_owner_only_mode(self):
        env = Path(self.tmp.name) / 'env'
        env.write_text('export KRX_API_KEY="k-123"\nANTHROPIC_API_KEY=a-456\nPATH=/evil\n$(touch pwned)\n')
        with patch.dict('os.environ', {'PATH': '/usr/bin'}, clear=True):
            env.chmod(0o644)
            self.assertEqual(mi_cli.load_env_file(env), [])          # readable by others: refused
            self.assertIn('chmod 600', self.err.getvalue())
            env.chmod(0o600)
            self.assertEqual(mi_cli.load_env_file(env), ['KRX_API_KEY', 'ANTHROPIC_API_KEY'])
            import os
            self.assertEqual(os.environ['KRX_API_KEY'], 'k-123')
            self.assertEqual(os.environ['PATH'], '/usr/bin')          # only the two secret names are loaded
            self.assertFalse((Path(self.tmp.name) / 'pwned').exists())
            os.environ['KRX_API_KEY'] = 'exported'
            mi_cli.load_env_file(env)
            self.assertEqual(os.environ['KRX_API_KEY'], 'exported')   # an exported value wins
        self.assertNotIn('k-123', self.err.getvalue())

    def test_regime_adds_daily_blocks_and_licence_aware_snapshots(self):
        import numpy as np
        import pandas as pd
        from mi import store
        days = pd.bdate_range('2025-06-02', periods=320)

        def series(conn, m, snap):
            close = 100 * np.exp(np.cumsum(np.random.default_rng(len(m) + ord(m[0])).normal(0, .01, 320)))
            return {'rows': [{'date': d.date().isoformat(), 'close': float(c)} for d, c in zip(days, close)],
                    'source_id': 'fred_sp500' if m in ('sp', 'nq') else 'krx_openapi', 'status': 'ok'}
        with patch('mi.prices.read_snapshot', return_value={'markets': {}}), patch('mi.prices.series', side_effect=series):
            mi_cli.cmd_regime(self.args())
            mi_cli.cmd_regime(self.args())                         # same data again: nothing new is stored
        out = json.loads((paths.DASHBOARD / 'regime.json').read_text())
        self.assertEqual(out['daily']['method'], 'daily-v1')
        self.assertIn('previous_as_of', out['markets']['ks']['daily'])
        with store.connect() as conn:
            rows = {r['market']: r for r in conn.execute('SELECT * FROM daily_snapshots')}
        self.assertEqual(sorted(rows), ['kq', 'ks', 'nq', 'sp'])   # one row each despite two runs
        self.assertEqual(rows['sp']['restricted'], 1)
        self.assertNotIn('risk_score', json.loads(rows['sp']['payload']))
        self.assertIn('risk_score', json.loads(rows['ks']['payload']))
        mi_cli.cmd_backup(self.args())
        self.assertEqual(len(list((paths.DATA / 'backups').glob('mi-*.sqlite'))), 1)
        mi_cli.cmd_status(self.args())
        status = json.loads((paths.DASHBOARD / 'status.json').read_text())
        self.assertEqual(status['storage']['integrity'], 'ok')
        self.assertEqual(status['storage']['rows']['daily_snapshots'], 4)

    def test_write_message(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mi_cli.write('x.json', {})
        self.assertTrue(out.getvalue().startswith('wrote '))


if __name__ == '__main__':
    unittest.main()
