#!/usr/bin/env python3
"""Market Intelligence pipeline CLI (v1.0). Run with the project venv: .venv/bin/python scripts/mi_cli.py <cmd>

  ingest     v0.2 price collector + archive storage-permitted rows + Fed macro series
  regime     v0.4 regime & risk analysis            -> data/dashboard/regime.json, macro.json
  train      v0.5/0.6 licence gate + walk-forward   -> data/dashboard/performance.json
  forecast   resolve pending outcomes + live forecast -> data/dashboard/forecast.json
  news       v0.7 news metadata + classification    -> data/dashboard/news.json
  briefing   v0.8 Korean daily briefing             -> data/dashboard/briefing.json
  status     system status (+ SQLite integrity)     -> data/dashboard/status.json
  backup     online copy of data/mi.sqlite         -> data/backups/ (never deletes old copies)
  morning | evening | weekly | all   composite jobs (see README schedule)
"""
import argparse
import json
import os
import subprocess
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect_data import atomic_json  # noqa: E402  (stdlib atomic writer from v0.2)
from mi import VERSION, briefing, daily, events, forecasting, licenses, macro, news, paths, prices, regime, store  # noqa: E402

OUT = lambda name: paths.DASHBOARD / name
FRED_RISK_TTL = timedelta(days=3)   # display-only FRED snapshot: ignored and removed once older than this
STALE_INPUT = timedelta(days=3)     # briefing flags inputs generated longer ago than this


def write(name, value):
    atomic_json(OUT(name), value)
    print('wrote %s' % (OUT(name).relative_to(paths.ROOT) if OUT(name).is_relative_to(paths.ROOT) else OUT(name)))


def read(name, default=None):
    try:
        return json.loads(OUT(name).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _age(stamp):
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None


def transient_risk():
    """FRED VIX / ICE display snapshot (opt-in). FRED terms restrict caching, so it is honoured only while
    fresh and deleted once it expires; it never enters training (dataset drops these features)."""
    snap = read('fred_risk.json') or {}
    age = _age(snap.get('fetched_at'))
    if snap and (age is None or age > FRED_RISK_TTL):
        OUT('fred_risk.json').unlink(missing_ok=True)
        return {}
    return {sid: [tuple(r) for r in rows] for sid, rows in (snap.get('series') or {}).items()}


def cmd_ingest(args):
    with store.job('ingest') as res:
        code, archive_error = 0, None
        if not args.skip_prices:
            # --output keeps MI_DATA_DIR experiments away from the real data/markets.json
            cmd = [sys.executable, str(Path(__file__).resolve().parent / 'collect_data.py'),
                   '--output', str(paths.MARKETS_JSON)]
            if args.markets:
                cmd += ['--markets', *args.markets]
            code = subprocess.run(cmd).returncode
        with store.connect() as conn:
            try:  # a broken snapshot must not stop the macro collection below
                archived = prices.ingest_snapshot(conn)
            except Exception as exc:
                archived, archive_error = None, '%s: %s' % (type(exc).__name__, exc)
                traceback.print_exc()
            results, transient = macro.collect(conn, with_fred_risk=args.with_fred_risk)
        if transient:
            write('fred_risk.json', {'note': '표시 전용 스냅샷 (보관 아님) · 사용자 옵트인 --with-fred-risk',
                                     'fetched_at': store.now_utc(), 'series': transient})
        elif OUT('fred_risk.json').exists() and not args.with_fred_risk:
            OUT('fred_risk.json').unlink()  # opt-in withdrawn: drop the display snapshot
        write('ingest.json', {'ran_at': store.now_utc(), 'price_collector_exit': code, 'archive': archived,
                              'archive_error': archive_error, 'macro': results})
        failed = [k for k, v in results.items() if v['status'] in ('stale', 'unavailable')]
        res['status'] = 'ok' if code == 0 and not failed and not archive_error else 'partial'
        res['message'] = 'prices exit %s; archive %s; macro failed: %s' % (
            code, 'error' if archive_error else 'ok', ','.join(failed) or 'none')
        print(res['message'])


def macro_rows(conn):
    rows = {sid: store.load_macro(conn, sid) for sid in macro.SERIES}
    rows.update(transient_risk())
    return rows


def cmd_regime(args):
    with store.job('regime') as res, store.connect() as conn:
        snap = prices.read_snapshot()
        mrows = macro_rows(conn)
        out = {'generated_at': store.now_utc(), 'engine_version': VERSION, 'method': 'rule-based-v1',
               'note': '국면은 현재 상태의 규칙 기반 서술이며 미래 수익률 예측이 아닙니다.', 'markets': {}}
        for m in prices.MARKETS:
            s = prices.series(conn, m, snap)
            r = regime.analyze(m, s['rows'], mrows)
            r.update(source_id=s['source_id'], data_status=s['status'], data_error=s.get('error'))
            r['daily'] = daily.market_block(r)
            out['markets'][m] = r
            print('%s: %s' % (m, r.get('regime') or r.get('status')))
        series = regime.global_risk(mrows)
        out['daily'] = {'method': daily.METHOD, 'thresholds': daily.THRESHOLDS, 'macro_changes': daily.macro_changes(series)}
        restricted = restricted_markets(out['markets'])
        saved = sum(store.save_daily(conn, daily.snapshot(m, r, m in restricted))
                    for m, r in out['markets'].items() if r.get('status') == 'ok')
        conn.commit()
        write('regime.json', out)
        write('macro.json', {'generated_at': store.now_utc(), 'series': series})
        res['message'] = ', '.join('%s=%s' % (m, r.get('regime') or r['status']) for m, r in out['markets'].items()) + \
            '; daily snapshots added %d' % saved


def cmd_train(args):
    with store.job('train') as res, store.connect() as conn:
        overrides = {'bootstrap': args.bootstrap} if args.bootstrap else None
        perf = forecasting.train(conn, markets=args.markets, cfg_overrides=overrides, transient_macro=transient_risk())
        perf['licenses'] = licenses.registry()
        write('performance.json', perf)
        trained = [m for m, hs in perf['markets'].items() if any(v.get('status') == 'ok' for v in hs.values())]
        res['status'] = 'ok' if trained else 'unavailable'
        res['message'] = 'trained: %s' % (','.join(trained) or 'none (licence gate)')
        print(res['message'])


def cmd_forecast(args):
    with store.job('forecast') as res, store.connect() as conn:
        resolved = forecasting.resolve_outcomes(conn)
        perf = read('performance.json') or {}
        out = forecasting.forecast(conn, perf, markets=args.markets, transient_macro=transient_risk())
        out['resolved_this_run'] = resolved
        out['history'] = forecasting.live_history(conn)        # recent rows for display
        out['live_summary'] = forecasting.live_summary(forecasting.evaluated_live(conn))  # whole history
        conn.commit()  # forecast.json must never claim rows the database does not hold
        write('forecast.json', out)
        n = sum(1 for hs in out['markets'].values() for f in hs.values() if f.get('status') != 'unavailable')
        res['status'] = 'ok' if n else 'unavailable'
        res['message'] = 'forecasts %d, outcomes resolved %d' % (n, resolved)
        print(res['message'])


def cmd_news(args):
    with store.job('news') as res, store.connect() as conn:
        report = news.collect(conn, use_gdelt=not args.no_gdelt)
        items = news.recent(conn)
        write('news.json', {'generated_at': store.now_utc(), 'report': report, 'items': items,
                            'sentiment_note': '영문 제목 키워드 휴리스틱(lexicon-en-v2) · 한국어 감성 미산출 · 참고용',
                            'model_licenses': licenses.MODELS,
                            'separation_note': '뉴스 분석은 예측 모델 입력으로 사용하지 않습니다.'})
        failed = [k for k, v in report.items() if isinstance(v, dict) and v.get('status') == 'failed']
        res['status'] = 'ok' if not failed else 'partial'
        res['message'] = 'added %d; failed: %s' % (report['added'], ','.join(failed) or 'none')
        print(res['message'])


def prev_forecast_changes(conn):
    out = []
    for m in prices.MARKETS:
        rows = conn.execute("SELECT input_cutoff, p_bull, model_name FROM forecasts WHERE market=? AND horizon=5 "
                            "ORDER BY input_cutoff DESC, id DESC", (m,)).fetchall()
        seen = []
        for r in rows:
            if r['input_cutoff'] not in [s['input_cutoff'] for s in seen] and (not seen or r['model_name'] == seen[0]['model_name']):
                seen.append(dict(r))
            if len(seen) == 2:
                break
        if len(seen) == 2:
            out.append({'market': m, 'horizon': 5, 'cur': seen[0]['p_bull'], 'prev': seen[1]['p_bull'],
                        'cur_cutoff': seen[0]['input_cutoff'], 'prev_cutoff': seen[1]['input_cutoff']})
    return out


def input_warnings(failed_stages=()):
    """Inputs the briefing had to reuse: a stage that failed in this run, or a file older than STALE_INPUT."""
    out = ['이번 실행에서 %s 단계 실패 · 이전 결과 사용' % s for s in failed_stages if s in ('regime', 'forecast', 'news')]
    for name in ('regime.json', 'forecast.json', 'news.json'):
        stamp = (read(name) or {}).get('generated_at')
        age = _age(stamp)
        if stamp is None:
            out.append('%s 없음' % name)
        elif age is None or age > STALE_INPUT:
            out.append('%s이 오래됨 (%s 생성)' % (name, stamp[:10]))
    return out


def restricted_markets(regimes):
    """Markets whose price source fails licenses.storage_allowed (e.g. FRED): numbers derived from them are
    shown on screen but never archived in SQLite or sent to an external API."""
    manifest = licenses.licensed_manifest()
    return {k for k, r in regimes.items()
            if r and r.get('source_id') and not licenses.storage_allowed(r['source_id'], manifest)}


def cmd_briefing(args):
    with store.job('briefing') as res, store.connect() as conn:
        reg_file = read('regime.json') or {}
        reg, macro_changes = reg_file.get('markets', {}), (reg_file.get('daily') or {}).get('macro_changes')
        mac = (read('macro.json') or {}).get('series', {})
        fc = read('forecast.json') or {}
        nw = (read('news.json') or {}).get('items', [])
        ev = events.upcoming()
        prev, now = prev_forecast_changes(conn), datetime.now(timezone.utc)
        brief = briefing.build(reg, mac, fc, nw, ev, prev, session=args.session, now=now, macro_changes=macro_changes)
        # the archived copy (and the LLM input) carries no numbers derived from sources that may not be stored
        kept = briefing.build(reg, mac, fc, nw, ev, prev, session=args.session, now=now,
                              restricted=restricted_markets(reg), macro_changes=macro_changes)
        llm_status = 'not requested'
        if args.llm:
            brief, llm_status = briefing.llm_rewrite(brief, facts_brief=kept)
            if llm_status == 'ok':
                kept['sections']['executive_summary'] = brief['sections']['executive_summary']
                kept['method'] = brief['method']
        for b in (brief, kept):
            b.update(events_status=ev['status'], next_fomc=ev['next_fomc'], llm_status=llm_status,
                     input_warnings=input_warnings(getattr(args, 'failed_stages', ())))
        conn.execute('INSERT INTO briefings (created_at, session, method, content) VALUES (?,?,?,?)',
                     (kept['generated_at'], kept['session'], kept['method'], store.dumps(kept)))
        conn.commit()
        write('briefing.json', brief)
        res['message'] = '%s · llm %s' % (brief['method'], llm_status)
        print(res['message'])


def cmd_status(args):
    with store.connect() as conn:
        try:  # a broken snapshot is reported in status.json instead of leaving a stale status page
            snap, snap_error = prices.read_snapshot() or {'markets': {}}, None
        except (OSError, ValueError) as exc:
            snap, snap_error = {'markets': {}}, '%s: %s' % (type(exc).__name__, exc)
        jobs = [dict(r) for r in conn.execute('SELECT * FROM job_runs ORDER BY id DESC LIMIT 40')]
        last = lambda name: next((j for j in jobs if j['job'] == name), None)
        perf = read('performance.json') or {}
        fc = read('forecast.json') or {}
        brief = read('briefing.json') or {}
        nw = read('news.json') or {}
        status = {
            'generated_at': store.now_utc(), 'engine_version': VERSION,
            'prices': {m: {k: (snap['markets'].get(m) or {}).get(k) for k in
                           ('status', 'as_of', 'updated_at', 'last_attempt_at', 'error', 'attribution')}
                       for m in prices.MARKETS},
            'snapshot_error': snap_error,
            'price_archive': {m: conn.execute('SELECT COUNT(*), MAX(date) FROM prices WHERE market=?', (m,)).fetchone()[:]
                              for m in prices.MARKETS},
            'macro': store.macro_meta(conn), 'ingest': read('ingest.json'),
            'model': {'trained_at': perf.get('generated_at'), 'gate': perf.get('gate'),
                      'trained_pairs': sum(1 for hs in (perf.get('markets') or {}).values()
                                           for v in hs.values() if v.get('status') == 'ok')},
            'forecast': {'generated_at': fc.get('generated_at'),
                         'live_records': conn.execute('SELECT COUNT(*) FROM forecasts').fetchone()[0],
                         'pending': conn.execute("SELECT COUNT(*) FROM forecasts WHERE status='pending'").fetchone()[0]},
            'briefing': {'generated_at': brief.get('generated_at'), 'method': brief.get('method'),
                         'llm_status': brief.get('llm_status')},
            'news': {'generated_at': nw.get('generated_at'), 'report': nw.get('report'),
                     'stored': conn.execute('SELECT COUNT(*) FROM news').fetchone()[0]},
            'jobs': jobs, 'last_jobs': {n: last(n) for n in ('ingest', 'regime', 'train', 'forecast', 'news', 'briefing')},
            'licenses': licenses.registry(),
            'storage': store.storage_report(conn),
        }
    write('status.json', status)


def cmd_backup(args):
    with store.connect() as conn:
        dest = store.backup(conn)
    print('backup: %s' % dest)


def run_stage(name, args):
    """Run one stage; return True on success. Failures are printed with a traceback, never raised."""
    try:
        COMMANDS[name](args)
        return True
    except Exception as exc:
        print('%s failed: %s' % (name, exc), file=sys.stderr)
        traceback.print_exc()
        return False


def run_all(names, args):
    """Exit code 1 when any stage raised (including the final status export); 'partial' / 'unavailable'
    job results are recorded in job_runs and status.json but are expected states, not failures."""
    args.failed_stages = []
    for name in names:
        print('== %s' % name)
        if not run_stage(name, args):  # one failed stage must not stop the others
            args.failed_stages.append(name)
    if not run_stage('status', args):
        args.failed_stages.append('status')
    if args.failed_stages:
        print('failed stages: %s' % ', '.join(args.failed_stages), file=sys.stderr)
    return 1 if args.failed_stages else 0


COMMANDS = {'ingest': cmd_ingest, 'regime': cmd_regime, 'train': cmd_train, 'forecast': cmd_forecast,
            'news': cmd_news, 'briefing': cmd_briefing, 'status': cmd_status, 'backup': cmd_backup}
COMPOSITE = {'morning': ['ingest', 'regime', 'forecast', 'news', 'briefing'],
             'evening': ['ingest', 'regime', 'forecast', 'news', 'briefing'],
             'weekly': ['ingest', 'regime', 'train', 'forecast'],
             'all': ['ingest', 'regime', 'train', 'forecast', 'news', 'briefing']}


SECRET_VARS = ('KRX_API_KEY', 'ANTHROPIC_API_KEY')


def load_env_file(path=None):
    """Read KRX_API_KEY / ANTHROPIC_API_KEY from ~/.config/mi/env (or MI_ENV_FILE) when not already exported.
    Lines are parsed as KEY=VALUE (an optional `export ` prefix and quotes are allowed); nothing is executed.
    The file must be owned by the current user and not group/other readable or writable. Returns loaded names."""
    path = Path(path or os.environ.get('MI_ENV_FILE') or Path.home() / '.config' / 'mi' / 'env')
    try:
        st = path.stat()
    except OSError:
        return []
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        print('env file ignored (%s): must be owned by you with mode 600 (chmod 600 %s)' % (path, path), file=sys.stderr)
        return []
    loaded = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line.startswith('export '):
            line = line[7:].strip()
        key, sep, value = line.partition('=')
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if sep and key in SECRET_VARS and value and not os.environ.get(key):
            os.environ[key] = value
            loaded.append(key)
    return loaded


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=list(COMMANDS) + list(COMPOSITE))
    p.add_argument('--markets', nargs='+', choices=list(prices.MARKETS))
    p.add_argument('--skip-prices', action='store_true', help='ingest: macro only')
    p.add_argument('--with-fred-risk', action='store_true',
                   help='ingest: also fetch FRED-hosted VIX / ICE spreads for display (terms restrict storage; opt-in)')
    p.add_argument('--no-gdelt', action='store_true')
    p.add_argument('--llm', action='store_true', help='briefing: paid Claude API rewrite of the summary (needs key)')
    p.add_argument('--session', choices=['morning', 'evening'])
    p.add_argument('--bootstrap', type=int, help='train: bootstrap replicates (default 500)')
    args = p.parse_args(argv)
    load_env_file()   # names only are ever printed; values stay in this process and its children
    paths.DASHBOARD.mkdir(parents=True, exist_ok=True)
    if args.command in COMPOSITE:
        if args.command in ('morning', 'evening') and not args.session:
            args.session = args.command
        return run_all(COMPOSITE[args.command], args)
    COMMANDS[args.command](args)
    if args.command != 'status' and not run_stage('status', args):
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
