"""Orchestration: licence gate -> dataset -> walk-forward evaluation -> live forecast -> outcome tracking.

Backtest predictions (backtest_* tables) and live forecasts (forecasts table, kind='live') are kept apart.
Live forecasts are append-only: a new model version adds rows, it never overwrites earlier ones.
"""
import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone

import numpy as np

from . import VERSION, dataset, explain, licenses, models, prices, store, timing, validation
from .macro import SERIES as MACRO

LABELS = dataset.CLASSES
LIVE_WARN_MIN_N = 30
MAX_INPUT_AGE_DAYS = 7   # a live forecast needs a recent close; older inputs mean collection is behind
OPENS = {'sp': (timing.ET, time(9, 30)), 'nq': (timing.ET, time(9, 30)),
         'ks': (timing.KST, time(9, 0)), 'kq': (timing.KST, time(9, 0))}


def first_outcome_open(market, cutoff):
    """Open of the earliest session that could follow `cutoff` (next weekday; holidays only make it later)."""
    tz, t = OPENS[market]
    d = cutoff + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return datetime.combine(d, t, tz).astimezone(timezone.utc)


def is_timely(row):
    """A live forecast counts only if it was recorded before the next session opened."""
    created = datetime.fromisoformat(row['created_at'])
    return created < first_outcome_open(row['market'], date.fromisoformat(row['input_cutoff']))


def load_inputs(conn, manifest=None, transient_macro=None):
    manifest = licenses.licensed_manifest() if manifest is None else manifest
    snap = prices.read_snapshot()
    series = {m: prices.series(conn, m, snap, manifest) for m in prices.MARKETS}
    # targets differ from series only for US markets with an ETF proxy; other markets' inputs (cross features)
    # keep using `series`, so adding a proxy never changes an existing model's feature set
    targets = {m: prices.target_series(conn, m, snap, manifest) for m in prices.MARKETS}
    macro_rows = {sid: store.load_macro(conn, sid) for sid in MACRO}
    for sid, rows in (transient_macro or {}).items():
        macro_rows[sid] = rows
    return series, targets, macro_rows, manifest


def gate_report(series, manifest):
    out = {}
    for m, s in series.items():
        ok, why = licenses.ml_allowed(s['source_id'], manifest)
        out[m] = {'source_id': s['source_id'], 'allowed': ok, 'reason': why,
                  'observations': len(s['rows'])}
    return out


def _version(name, params, calibration, feature_hash, horizon, train_end):
    h = hashlib.sha1(json.dumps([name, params, calibration, feature_hash, horizon, VERSION],
                                sort_keys=True).encode()).hexdigest()[:8]
    return '%s-h%d-%s-%s' % (name, horizon, h, train_end)


def train(conn, markets=None, horizons=dataset.HORIZONS, cfg_overrides=None, manifest=None, log=print,
          transient_macro=None):
    """Walk-forward evaluation for every (market, horizon) that passes the gate. Returns performance dict."""
    bad = sorted(set(cfg_overrides or {}) & set(validation.GATE_FIELDS))  # gate criteria are fixed a priori
    if bad:
        raise ValueError('검증 설정은 사전 고정 · 실행 중 변경 불가: %s' % ', '.join(bad))
    series, targets, macro_rows, manifest = load_inputs(conn, manifest, transient_macro)
    gates = gate_report(targets, manifest)
    perf = {'generated_at': store.now_utc(), 'engine_version': VERSION, 'gate': gates, 'markets': {}}
    for m in markets or prices.MARKETS:
        perf['markets'][m] = {}
        for h in horizons:
            if not gates[m]['allowed']:
                perf['markets'][m][str(h)] = {'status': 'unavailable', 'reason': gates[m]['reason']}
                continue
            ds = dataset.build(m, dict(series, **{m: targets[m]}), macro_rows, h, manifest)
            fixed = store.holdout_start(conn, m, h)
            cfg = validation.Config(horizon=h, holdout_start=fixed or '', **(cfg_overrides or {}))
            log('%s %dD: %d rows, %d features' % (m, h, len(ds['X']), ds['X'].shape[1]))
            res = validation.evaluate(ds, cfg, log)
            entry = {k: v for k, v in res.items() if k not in ('predictions', 'chosen')}
            entry.update(features=list(ds['X'].columns), dropped_features=ds['dropped_features'],
                         feature_sources=ds['feature_sources'], label_rule={
                             'k': dataset.NEUTRAL_K, 'vol_window': dataset.LABEL_VOL_WINDOW,
                             'definition': 'R=P[t+h]/P[t]-1; |R|<=k·σ60·√h → neutral'},
                         config={k: v for k, v in cfg.__dict__.items() if k != 'extras'})
            if res['status'] == 'ok':
                run_id = 'bt-%s-%d-%s' % (m, h, datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
                entry['run_id'] = run_id
                if not fixed:
                    store.fix_holdout_start(conn, m, h, res['spans']['holdout'][0])
                entry['holdout_fixed'] = store.holdout_start(conn, m, h)
                entry['previous_run'] = _previous_verdict(conn, m, h)
                if 'lightgbm' in res['chosen']:
                    entry['lightgbm_importance'] = _importance(ds, res, cfg)
                _store_backtest(conn, run_id, m, h, ds, res, entry)
            perf['markets'][m][str(h)] = entry
        conn.commit()  # release the write lock between markets; each market's rows are self-contained
    return perf


def _previous_verdict(conn, market, horizon):
    """Latest earlier backtest verdict for the same pair, so verdict changes across re-runs stay visible."""
    row = conn.execute('SELECT run_id, created_at, summary FROM backtest_runs WHERE market=? AND horizon=? '
                       'ORDER BY created_at DESC LIMIT 1', (market, horizon)).fetchone()
    if not row:
        return None
    summ = json.loads(row['summary'])
    primary = summ.get('primary_model')
    return {'run_id': row['run_id'], 'created_at': row['created_at'], 'primary_model': primary,
            'status': ((summ.get('models') or {}).get(primary) or {}).get('validation', {}).get('status')}


def _importance(ds, res, cfg):
    """Gain importance of a LightGBM refit on the development span only (descriptive)."""
    X, y = ds['X'], ds['y']
    labelled = y.notna().to_numpy()
    sp = validation.spans(len(X), ds['outcome_pos'], labelled, cfg, [d.date().isoformat() for d in X.index])
    idx = validation.usable_before(sp[1], ds['outcome_pos'], labelled, cfg)
    model = models.LightGBM(**res['chosen']['lightgbm']['params']).fit(X.iloc[idx], y.to_numpy()[idx])
    return model.importance(list(X.columns))[:12]


def _store_backtest(conn, run_id, market, horizon, ds, res, entry):
    summary = {k: v for k, v in entry.items() if k not in ('feature_sources',)}
    conn.execute('INSERT INTO backtest_runs VALUES (?,?,?,?,?,?)',
                 (run_id, store.now_utc(), market, horizon, store.dumps(entry['config']), store.dumps(summary)))
    dates = [d.date().isoformat() for d in ds['X'].index]
    for name, (idx, p, yt, fold_ids) in res['predictions'].items():
        conn.executemany('INSERT INTO backtest_predictions VALUES (?,?,?,?,?,?,?,?)',
                         [(run_id, name, dates[i], float(q[2]), float(q[1]), float(q[0]), LABELS[int(t)], int(f))
                          for i, q, t, f in zip(idx, p, yt, fold_ids)])


def _expected_outcome(cutoff, horizon):
    d, n = cutoff, 0
    while n < horizon:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return d


def forecast(conn, perf, markets=None, manifest=None, log=print, transient_macro=None, now=None):
    """Fit the development-chosen primary model on all labelled data and forecast the latest row."""
    series, targets, macro_rows, manifest = load_inputs(conn, manifest, transient_macro)
    out = {'generated_at': store.now_utc(), 'markets': {}}
    for m in markets or prices.MARKETS:
        out['markets'][m] = {}
        for h in dataset.HORIZONS:
            p = (perf.get('markets', {}).get(m) or {}).get(str(h)) or {}
            if p.get('status') != 'ok':
                out['markets'][m][str(h)] = {'market': m, 'horizon': h, 'status': 'unavailable',
                                             'reason': p.get('reason', '검증된 학습 결과 없음 (train 먼저 실행)')}
                continue
            out['markets'][m][str(h)] = _forecast_one(conn, m, h, p, dict(series, **{m: targets[m]}), macro_rows, manifest, log,
                                                      now or datetime.now(timezone.utc))
    return out


def _forecast_one(conn, m, h, perf_entry, series, macro_rows, manifest, log, now):
    ds = dataset.build(m, series, macro_rows, h, manifest)
    name = perf_entry['primary_model']
    chosen = perf_entry['models'][name]
    cfg = validation.Config(horizon=h, **{k: v for k, v in perf_entry['config'].items() if k != 'horizon'})
    X, y = ds['X'], ds['y']
    labelled = y.notna().to_numpy()
    n = len(X)
    # Same training-window rule as the walk-forward folds (rolling when cfg.window > 0).
    lo = max(0, n - 1 - cfg.window) if cfg.window else 0
    train = validation.usable_before(n - 1, ds['outcome_pos'], labelled, cfg, lo)
    if list(X.columns) != perf_entry['features']:
        return {'market': m, 'horizon': h, 'status': 'unavailable', 'reason': '피처 구성이 검증 시점과 다름 · 재학습 필요'}
    if len(train) < cfg.min_train:
        return {'market': m, 'horizon': h, 'status': 'unavailable',
                'reason': '학습 표본 %d < 최소 %d' % (len(train), cfg.min_train)}
    age = (now.astimezone(timing.KST).date() - X.index[-1].date()).days
    if age > MAX_INPUT_AGE_DAYS:
        return {'market': m, 'horizon': h, 'status': 'unavailable',
                'reason': '입력 데이터가 오래됨 (마지막 종가 %s, %d일 전) · 수집·백필 확인' % (X.index[-1].date(), age)}
    cut = train[int(len(train) * (1 - cfg.calib_frac))]
    cal = train[train >= cut]
    fit = train[(train < cut) & (ds['outcome_pos'][train] + cfg.embargo <= cut)]
    model = models.FACTORY[name](**chosen['params']).fit(X.iloc[fit], y.to_numpy()[fit])
    calib = models.CALIBRATORS[chosen['calibration']]().fit(model.predict_proba(X.iloc[cal]), y.to_numpy()[cal])
    q = calib.transform(model.predict_proba(X.iloc[[n - 1]]))[0]
    cutoff = X.index[-1].date()
    train_end = X.index[train[-1]].date().isoformat()
    version = _version(name, chosen['params'], chosen['calibration'], ds['feature_hash'], h, train_end)
    status = chosen['validation']['status']
    rec = {'market': m, 'horizon': h, 'bullish_probability': round(float(q[2]), 4),
           'neutral_probability': round(float(q[1]), 4), 'bearish_probability': round(float(q[0]), 4),
           'model_name': name, 'model_version': version, 'status': status,
           'validation_reasons': chosen['validation']['reasons'], 'input_cutoff': cutoff.isoformat(),
           'decision_time_utc': timing.decision_time(m, cutoff).isoformat(),
           'expected_outcome_date': _expected_outcome(cutoff, h).isoformat(),
           'expected_outcome_note': '주말만 제외한 추정일 · 휴장일 미반영 (실제 판정은 h번째 관측 종가)',
           'threshold': round(float(ds['threshold'].iloc[-1]), 6), 'base_close': float(ds['close'].iloc[-1]),
           'features': {c: (None if not np.isfinite(v) else round(float(v), 4)) for c, v in X.iloc[-1].items()},
           'provenance': {'target_source': series[m]['source_id'], 'proxy': series[m].get('proxy'),
                          'provisional_input': X.index[-1].date().isoformat() in (series[m].get('provisional') or []),
                          'feature_sources': sorted(set(ds['feature_sources'].values())),
                          'backtest_run': perf_entry.get('run_id'), 'engine_version': VERSION}}
    if status == 'validated' and n >= 2:   # outcome-change explanation only for a model that passed the gate
        predict = lambda x: calib.transform(model.predict_proba(x))
        att = explain.attribute(predict, X.iloc[[n - 1]], X.iloc[[n - 2]])
        rec['explanation'] = explain.summary(att, X.index[-2].date().isoformat(), cutoff.isoformat())
    else:
        rec['explanation'] = {'status': 'not_computed', 'reason': '검증을 통과한 모델에만 전망 변화 설명을 만듭니다.'}
    s = sum(rec[k] for k in ('bullish_probability', 'neutral_probability', 'bearish_probability'))
    if abs(s - 1) >= 1e-3:
        raise ValueError('probabilities must sum to 1 (got %.6f)' % s)
    cur = conn.execute('INSERT OR IGNORE INTO forecasts (kind, created_at, market, horizon, model_name, model_version, '
                       'input_cutoff, decision_time_utc, p_bull, p_neutral, p_bear, threshold, base_close, '
                       'expected_outcome_date, validation_status, status, provenance) '
                       'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('live', now.isoformat(timespec='seconds'), m, h, name, version, rec['input_cutoff'], rec['decision_time_utc'],
                        float(q[2]), float(q[1]), float(q[0]), rec['threshold'], rec['base_close'],
                        rec['expected_outcome_date'], status, 'pending', store.dumps(rec['provenance'])))
    rec['stored'] = 'new' if cur.rowcount else 'already_recorded'
    rec['timely'] = is_timely({'created_at': now.isoformat(timespec='seconds'), 'input_cutoff': rec['input_cutoff'], 'market': m})
    log('%s %dD %s: bull %.2f neutral %.2f bear %.2f (%s, %s)' % (m, h, name, q[2], q[1], q[0], status,
                                                                 rec['stored']))
    return rec


def resolve_outcomes(conn, manifest=None):
    """Mark pending live forecasts once h further closes exist. Uses the threshold stored at forecast time."""
    manifest = licenses.licensed_manifest() if manifest is None else manifest
    snap = prices.read_snapshot()
    cache, done = {}, 0
    for f in conn.execute("SELECT * FROM forecasts WHERE status = 'pending'").fetchall():
        prov = json.loads(f['provenance'])
        if f['market'] not in cache:
            cache[f['market']] = prices.target_series(conn, f['market'], snap, manifest)
        s = cache[f['market']]
        if s['source_id'] != prov['target_source']:
            continue  # never score a forecast against a different price source
        dates = [r['date'] for r in s['rows']]
        if f['input_cutoff'] not in dates:
            continue
        i = dates.index(f['input_cutoff'])
        if i + f['horizon'] >= len(dates):
            continue
        out = s['rows'][i + f['horizon']]
        if out['date'] in (s.get('provisional') or []):
            continue  # score only against the official close (KRX publishes the next morning)
        ret = out['close'] / f['base_close'] - 1
        cls = 'bullish' if ret > f['threshold'] else 'bearish' if ret < -f['threshold'] else 'neutral'
        conn.execute("UPDATE forecasts SET status='evaluated', outcome_date=?, actual_return=?, actual_class=?, "
                     "evaluated_at=? WHERE id=? AND status='pending'",
                     (out['date'], ret, cls, store.now_utc(), f['id']))
        done += 1
    return done


def live_history(conn, limit=400):
    rows = [dict(r) for r in conn.execute('SELECT * FROM forecasts ORDER BY input_cutoff DESC, id DESC LIMIT ?',
                                          (limit,))]
    for r in rows:
        r['provenance'] = json.loads(r['provenance'])
        probs = {'bearish': r['p_bear'], 'neutral': r['p_neutral'], 'bullish': r['p_bull']}
        r['predicted_class'] = max(probs, key=probs.get)
        r['hit'] = None if r['status'] != 'evaluated' else r['predicted_class'] == r['actual_class']
        r['late'] = not is_timely(r)
    return rows


BRIER_UNIFORM = 2 / 3  # Brier score of the uninformative 1/3-1/3-1/3 forecast (3 classes)


def evaluated_live(conn):
    """All evaluated live forecasts, one per (market, horizon, input_cutoff): the first one recorded.
    A retrain that changes model_version may append a second row for the same cutoff; it is kept in the
    table (append-only) but never counted twice."""
    rows = [dict(r) for r in conn.execute(
        "SELECT f.* FROM forecasts f JOIN (SELECT MIN(id) AS id FROM forecasts WHERE status = 'evaluated' "
        "GROUP BY market, horizon, input_cutoff) first ON first.id = f.id ORDER BY f.id")]
    return [r for r in rows if is_timely(r)]   # a forecast recorded after the next open is not a forecast


def live_summary(rows):
    """rows: evaluated live forecasts (use evaluated_live(conn) so the summary covers the whole history)."""
    out, seen = {}, set()
    for r in rows:
        if r['status'] != 'evaluated' or (r['market'], r['horizon'], r['input_cutoff']) in seen:
            continue
        seen.add((r['market'], r['horizon'], r['input_cutoff']))
        key = '%s|%d' % (r['market'], r['horizon'])
        e = out.setdefault(key, {'market': r['market'], 'horizon': r['horizon'], 'n': 0, 'hits': 0, 'brier_sum': 0.0})
        y = np.array([r['actual_class'] == c for c in LABELS], float)
        p = np.array([r['p_bear'], r['p_neutral'], r['p_bull']])
        e['n'] += 1
        e['hits'] += int(p.argmax() == y.argmax())
        e['brier_sum'] += float(((p - y) ** 2).sum())
    for e in out.values():
        e['accuracy'] = e['hits'] / e['n']
        e['brier'] = e.pop('brier_sum') / e['n']
        e['brier_uniform'] = round(BRIER_UNIFORM, 4)
        e['skill_vs_uniform'] = 1 - e['brier'] / BRIER_UNIFORM
        e['warning'] = None
        if e['n'] < LIVE_WARN_MIN_N:
            e['warning'] = '실시간 표본 %d건 < %d · 성과 판단 보류' % (e['n'], LIVE_WARN_MIN_N)
        elif e['skill_vs_uniform'] <= 0:
            e['warning'] = '실시간 Brier가 균등 확률(1/3)보다 나쁨 · 재검증 필요'
    return list(out.values())
