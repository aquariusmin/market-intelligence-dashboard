#!/usr/bin/env python3
"""Market Intelligence pipeline CLI (v1.0). Run with the project venv: .venv/bin/python scripts/mi_cli.py <cmd>

  ingest     v0.2 price collector + archive storage-permitted rows + Fed macro series
  regime     v0.4 regime & risk analysis            -> data/dashboard/regime.json, macro.json
  train      v0.5/0.6 licence gate + walk-forward   -> data/dashboard/performance.json
  forecast   resolve pending outcomes + live forecast -> data/dashboard/forecast.json
  news       v0.7 news metadata + classification    -> data/dashboard/news.json
  briefing   v0.8 Korean daily briefing             -> data/dashboard/briefing.json
  status     system status                          -> data/dashboard/status.json
  morning | evening | weekly | all   composite jobs (see README schedule)
"""
import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect_data import atomic_json  # noqa: E402  (stdlib atomic writer from v0.2)
from mi import VERSION, briefing, events, forecasting, licenses, macro, news, paths, prices, regime, store  # noqa: E402

OUT = lambda name: paths.DASHBOARD / name


def write(name, value):
    atomic_json(OUT(name), value)
    print('wrote %s' % OUT(name).relative_to(paths.ROOT) if OUT(name).is_relative_to(paths.ROOT) else OUT(name))


def read(name, default=None):
    try:
        return json.loads(OUT(name).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def transient_risk():
    snap = read('fred_risk.json') or {}
    return {sid: [tuple(r) for r in rows] for sid, rows in (snap.get('series') or {}).items()}


def cmd_ingest(args):
    with store.job('ingest') as res:
        code = 0
        if not args.skip_prices:
            cmd = [sys.executable, str(Path(__file__).resolve().parent / 'collect_data.py')]
            if args.markets:
                cmd += ['--markets', *args.markets]
            code = subprocess.run(cmd).returncode
        with store.connect() as conn:
            archived = prices.ingest_snapshot(conn)
            results, transient = macro.collect(conn, with_fred_risk=args.with_fred_risk)
        if transient:
            write('fred_risk.json', {'note': '표시 전용 스냅샷 (보관 아님) · 사용자 옵트인 --with-fred-risk',
                                     'fetched_at': store.now_utc(), 'series': transient})
        elif OUT('fred_risk.json').exists() and not args.with_fred_risk:
            OUT('fred_risk.json').unlink()  # opt-in withdrawn: drop the display snapshot
        write('ingest.json', {'ran_at': store.now_utc(), 'price_collector_exit': code, 'archive': archived,
                              'macro': results})
        failed = [k for k, v in results.items() if v['status'] in ('stale', 'unavailable')]
        res['status'] = 'ok' if code == 0 and not failed else 'partial'
        res['message'] = 'prices exit %s; macro failed: %s' % (code, ','.join(failed) or 'none')
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
            out['markets'][m] = r
            print('%s: %s' % (m, r.get('regime') or r.get('status')))
        write('regime.json', out)
        write('macro.json', {'generated_at': store.now_utc(), 'series': regime.global_risk(mrows)})
        res['message'] = ', '.join('%s=%s' % (m, r.get('regime') or r['status']) for m, r in out['markets'].items())


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
        hist = forecasting.live_history(conn)
        out['resolved_this_run'] = resolved
        out['history'] = hist
        out['live_summary'] = forecasting.live_summary(hist)
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
                            'sentiment_note': '영문 제목 키워드 휴리스틱(lexicon-en-v1) · 한국어 감성 미산출 · 참고용',
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


def cmd_briefing(args):
    with store.job('briefing') as res, store.connect() as conn:
        reg = (read('regime.json') or {}).get('markets', {})
        mac = (read('macro.json') or {}).get('series', {})
        fc = read('forecast.json') or {}
        nw = (read('news.json') or {}).get('items', [])
        ev = events.upcoming()
        brief = briefing.build(reg, mac, fc, nw, ev, prev_forecast_changes(conn), session=args.session)
        brief['events_status'] = ev['status']
        brief['next_fomc'] = ev['next_fomc']
        llm_status = 'not requested'
        if args.llm:
            brief, llm_status = briefing.llm_rewrite(brief)
        brief['llm_status'] = llm_status
        conn.execute('INSERT INTO briefings (created_at, session, method, content) VALUES (?,?,?,?)',
                     (brief['generated_at'], brief['session'], brief['method'], store.dumps(brief)))
        write('briefing.json', brief)
        res['message'] = '%s · llm %s' % (brief['method'], llm_status)
        print(res['message'])


def cmd_status(args):
    with store.connect() as conn:
        snap = prices.read_snapshot() or {'markets': {}}
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
        }
    write('status.json', status)


def run_all(names, args):
    failures = []
    for name in names:
        print('== %s' % name)
        try:
            COMMANDS[name](args)
        except Exception as exc:  # one failed stage must not stop the others
            failures.append(name)
            print('%s failed: %s' % (name, exc), file=sys.stderr)
    cmd_status(args)
    return 1 if failures else 0


COMMANDS = {'ingest': cmd_ingest, 'regime': cmd_regime, 'train': cmd_train, 'forecast': cmd_forecast,
            'news': cmd_news, 'briefing': cmd_briefing, 'status': cmd_status}
COMPOSITE = {'morning': ['ingest', 'regime', 'forecast', 'news', 'briefing'],
             'evening': ['ingest', 'regime', 'forecast', 'news', 'briefing'],
             'weekly': ['ingest', 'regime', 'train', 'forecast'],
             'all': ['ingest', 'regime', 'train', 'forecast', 'news', 'briefing']}


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
    paths.DASHBOARD.mkdir(parents=True, exist_ok=True)
    if args.command in COMPOSITE:
        if args.command in ('morning', 'evening') and not args.session:
            args.session = args.command
        return run_all(COMPOSITE[args.command], args)
    COMMANDS[args.command](args)
    if args.command != 'status':
        cmd_status(args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
