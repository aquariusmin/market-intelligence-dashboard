"""v0.6 time-series validation. No random splits anywhere.

Timeline (positions in the market's own trading-day index):

  |---- development (tuning + calibration-method choice) ----|-purge-|------ holdout (reported) ------|

Within every walk-forward fold the training slice is split chronologically again:

  [ fit .......... ] -purge- [ calibration ] -purge- [ test block (step days) ]

Purging: a sample at position i is usable before position s only if its outcome position i + h + embargo <= s,
so overlapping 5D / 20D forward windows never straddle fit / calibration / test boundaries.
"""
import math
from dataclasses import dataclass, field

import numpy as np
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix, f1_score

from . import models

K = 3


@dataclass
class Config:
    horizon: int
    min_train: int = 504          # ~2 years of labelled history before the first test block
    step: int = 21                # refit cadence (~monthly)
    calib_frac: float = 0.25      # last quarter of each training slice calibrates probabilities
    embargo: int = 1
    window: int = 0               # 0 = expanding; >0 = rolling window length
    dev_frac: float = 0.4         # share of evaluable span used for tuning; rest = untouched holdout
    min_effective_holdout: int = 50
    bootstrap: int = 500
    max_ece: float = 0.08
    min_bal_acc_gain: float = 0.02  # directional information beyond the base rate, not just sharper probabilities
    seed: int = 0
    extras: dict = field(default_factory=dict)


# ---------------- metrics ----------------

def brier(p, y):
    onehot = np.eye(K)[np.asarray(y, int)]
    return float(((p - onehot) ** 2).sum(1).mean())


def log_loss(p, y):
    return float(-np.log(np.clip(p[np.arange(len(y)), np.asarray(y, int)], 1e-12, 1)).mean())


def reliability(p, y, bins=10):
    """Per-class reliability (predicted prob bin vs observed frequency) and expected calibration error."""
    y = np.asarray(y, int)
    out, ece_terms = {}, []
    edges = np.linspace(0, 1, bins + 1)
    for k, name in enumerate(['bearish', 'neutral', 'bullish']):
        rows = []
        idx = np.clip(np.digitize(p[:, k], edges) - 1, 0, bins - 1)
        for b in range(bins):
            m = idx == b
            if m.sum() == 0:
                continue
            pred, obs = float(p[m, k].mean()), float((y[m] == k).mean())
            rows.append({'bin': b, 'predicted': round(pred, 4), 'observed': round(obs, 4), 'n': int(m.sum())})
            ece_terms.append(m.sum() * abs(pred - obs))
        out[name] = rows
    ece = float(sum(ece_terms) / (len(y) * K)) if len(y) else None
    return out, ece


def metrics(p, y):
    y = np.asarray(y, int)
    pred = p.argmax(1)
    rel, ece = reliability(p, y)
    return {'n': int(len(y)), 'accuracy': float(accuracy_score(y, pred)),
            'balanced_accuracy': float(balanced_accuracy_score(y, pred)),
            'macro_f1': float(f1_score(y, pred, average='macro', labels=[0, 1, 2], zero_division=0)),
            'brier': brier(p, y), 'log_loss': log_loss(p, y), 'ece': ece,
            'confusion_matrix': confusion_matrix(y, pred, labels=[0, 1, 2]).tolist(),
            'class_share': (np.bincount(y, minlength=K) / max(len(y), 1)).round(4).tolist(),
            'reliability': rel}


def rolling_accuracy(dates, p, y, window=63):
    hit = (p.argmax(1) == np.asarray(y, int)).astype(float)
    out = []
    for i in range(window - 1, len(hit), 5):
        out.append({'date': dates[i], 'accuracy': round(float(hit[i - window + 1:i + 1].mean()), 4)})
    return out


def block_bootstrap_diff(loss_a, loss_b, block, reps, seed=0):
    """Moving-block bootstrap CI of mean(loss_a - loss_b); positive = b is better.
    Blocks >= horizon respect the serial dependence created by overlapping forward returns."""
    d = np.asarray(loss_a) - np.asarray(loss_b)
    n = len(d)
    if n < 2 * block:
        return float(d.mean()) if n else None, None, None
    rng = np.random.default_rng(seed)
    starts = np.arange(n - block + 1)
    k = int(math.ceil(n / block))
    means = np.empty(reps)
    for r in range(reps):
        idx = (rng.choice(starts, k)[:, None] + np.arange(block)).ravel()[:n]
        means[r] = d[idx].mean()
    return float(d.mean()), float(np.quantile(means, 0.05)), float(np.quantile(means, 0.95))


def per_sample_logloss(p, y):
    return -np.log(np.clip(p[np.arange(len(y)), np.asarray(y, int)], 1e-12, 1))


# ---------------- splitting ----------------

def usable_before(s, outcome_pos, labelled, cfg, lo=0):
    """Indices i in [lo, s) whose label exists and whose outcome is observed (purged) before s."""
    i = np.arange(lo, s)
    return i[labelled[i] & (outcome_pos[i] + cfg.embargo <= s)]


def folds(n, outcome_pos, labelled, cfg, start, end):
    """Yield (fit_idx, cal_idx, test_idx) for test blocks covering [start, end)."""
    for s in range(start, end, cfg.step):
        test = np.arange(s, min(s + cfg.step, end))
        test = test[labelled[test]]
        if not len(test):
            continue
        lo = max(0, s - cfg.window) if cfg.window else 0
        train = usable_before(s, outcome_pos, labelled, cfg, lo)
        if len(train) < cfg.min_train:
            continue
        cut = train[int(len(train) * (1 - cfg.calib_frac))]
        cal = train[train >= cut]
        fit = train[(train < cut) & (outcome_pos[train] + cfg.embargo <= cut)]
        if len(fit) < cfg.min_train * (1 - cfg.calib_frac) * 0.8 or len(cal) < 50:
            continue
        yield fit, cal, test


def spans(n, outcome_pos, labelled, cfg):
    """(dev_start, dev_end, hold_start, hold_end): dev and holdout separated by a purge gap."""
    lab = np.flatnonzero(labelled)
    if not len(lab):
        return None
    # earliest position with min_train usable samples before it
    first = None
    for s in range(lab[0], n):
        if len(usable_before(s, outcome_pos, labelled, cfg)) >= cfg.min_train:
            first = s
            break
    if first is None:
        return None
    last = lab[-1] + 1
    dev_end = first + int((last - first) * cfg.dev_frac)
    hold_start = dev_end + cfg.horizon + cfg.embargo
    if hold_start >= last:
        return None
    return first, dev_end, hold_start, last


# ---------------- walk-forward ----------------

def run_model(name, params, X, y, outcome_pos, labelled, cfg, start, end, calibrators=None):
    """Walk-forward predictions for one model config; returns {calibrator: (idx, proba, fold_ids)}."""
    calibrators = calibrators or (list(models.CALIBRATORS) if name in models.CALIBRATED else ['none'])
    out = {c: ([], [], []) for c in calibrators}
    yv = y.to_numpy()
    for f, (fit, cal, test) in enumerate(folds(len(X), outcome_pos, labelled, cfg, start, end)):
        model = models.FACTORY[name](**params).fit(X.iloc[fit], yv[fit])
        p_cal, p_test = model.predict_proba(X.iloc[cal]), model.predict_proba(X.iloc[test])
        for c in calibrators:
            calib = models.CALIBRATORS[c]().fit(p_cal, yv[cal])
            q = calib.transform(p_test)
            out[c][0].append(test)
            out[c][1].append(q)
            out[c][2].append(np.full(len(test), f))
    return {c: (np.concatenate(a) if a else np.array([], int), np.vstack(b) if b else np.empty((0, K)),
                np.concatenate(fi) if fi else np.array([], int)) for c, (a, b, fi) in out.items()}


def evaluate(ds, cfg, log=lambda *a: None):
    """Tune on development span, report on untouched holdout. Returns JSON-ready summary + predictions."""
    X, y = ds['X'], ds['y']
    labelled = y.notna().to_numpy()
    outcome_pos = ds['outcome_pos']
    sp = spans(len(X), outcome_pos, labelled, cfg)
    if sp is None:
        return {'status': 'insufficient_history', 'reason': '최소 학습 표본(%d) + 평가 구간 확보 불가' % cfg.min_train}
    dev_start, dev_end, hold_start, hold_end = sp
    dates = [d.date().isoformat() for d in X.index]
    chosen = {}
    # 1) development: choose hyper-parameters and calibration method by mean log loss
    for name, grid in models.GRID.items():
        best = None
        for params in grid:
            res = run_model(name, params, X, y, outcome_pos, labelled, cfg, dev_start, dev_end)
            for cal, (idx, p, _) in res.items():
                if not len(idx):
                    continue
                ll = log_loss(p, y.to_numpy()[idx])
                if best is None or ll < best['dev_log_loss']:
                    best = {'params': params, 'calibration': cal, 'dev_log_loss': ll, 'dev_n': int(len(idx))}
        if best:
            chosen[name] = best
            log('  dev %-20s %s cal=%s ll=%.4f' % (name, best['params'], best['calibration'], best['dev_log_loss']))
    if not chosen:
        return {'status': 'insufficient_history', 'reason': '개발 구간 fold 없음'}
    # 2) holdout: every model with its development-chosen config, identical folds
    results, preds = {}, {}
    for name, c in chosen.items():
        res = run_model(name, c['params'], X, y, outcome_pos, labelled, cfg, hold_start, hold_end,
                        calibrators=[c['calibration']])
        idx, p, fold_ids = res[c['calibration']]
        if not len(idx):
            continue
        yt = y.to_numpy()[idx].astype(int)
        results[name] = dict(metrics(p, yt), params=c['params'], calibration=c['calibration'],
                             dev_log_loss=c['dev_log_loss'], family=models.FACTORY[name].family,
                             rolling_accuracy=rolling_accuracy([dates[i] for i in idx], p, yt))
        preds[name] = (idx, p, yt, fold_ids)
    if 'class_frequency' not in results:
        return {'status': 'insufficient_history', 'reason': '기준 모델 holdout 예측 없음'}
    base_idx, base_p, base_y, _ = preds['class_frequency']
    base_loss = pd_align(base_idx, per_sample_logloss(base_p, base_y))
    block = max(cfg.horizon, 10)
    for name, r in results.items():
        idx, p, yt, _ = preds[name]
        common = np.intersect1d(idx, base_idx)
        mine = pd_align(idx, per_sample_logloss(p, yt))
        a = np.array([base_loss[i] for i in common])
        b = np.array([mine[i] for i in common])
        mean, lo, hi = block_bootstrap_diff(a, b, block, cfg.bootstrap, cfg.seed)
        r['vs_class_frequency'] = {
            'log_loss_improvement': mean, 'ci90_low': lo, 'ci90_high': hi,
            'brier_skill': 1 - r['brier'] / results['class_frequency']['brier'],
            'balanced_accuracy_delta': r['balanced_accuracy'] - results['class_frequency']['balanced_accuracy']}
        r['effective_n'] = int(r['n'] // cfg.horizon)
        r['validation'] = gate(r, name, cfg)
    primary = min((n for n in chosen if n != 'class_frequency'), key=lambda n: chosen[n]['dev_log_loss'],
                  default='class_frequency')
    return {'status': 'ok', 'horizon': cfg.horizon,
            'spans': {'development': [dates[dev_start], dates[dev_end - 1]],
                      'holdout': [dates[hold_start], dates[hold_end - 1]]},
            'primary_model': primary, 'primary_rule': '개발 구간 log loss 최저 (holdout 미사용)',
            'models': results, 'predictions': {n: (v[0], v[1], v[2], v[3]) for n, v in preds.items()},
            'chosen': chosen}


def pd_align(idx, values):
    return dict(zip(idx.tolist(), values.tolist()))


def gate(r, name, cfg):
    """Fixed, a-priori validation criteria. Anything else is shown as NOT VALIDATED."""
    reasons = []
    if name == 'class_frequency':
        return {'status': 'baseline', 'reasons': ['기준 모델']}
    if r['effective_n'] < cfg.min_effective_holdout:
        reasons.append('비중첩 holdout 표본 %d < %d' % (r['effective_n'], cfg.min_effective_holdout))
    v = r['vs_class_frequency']
    if v['ci90_low'] is None or v['ci90_low'] <= 0:
        reasons.append('기준 모델 대비 log loss 개선의 90%% 하한 ≤ 0')
    if v['brier_skill'] <= 0:
        reasons.append('Brier skill ≤ 0')
    if v['balanced_accuracy_delta'] < cfg.min_bal_acc_gain:
        reasons.append('균형 정확도 개선 %+.1f%%p < %.0f%%p (방향 정보 부족)'
                       % (v['balanced_accuracy_delta'] * 100, cfg.min_bal_acc_gain * 100))
    if r['ece'] is None or r['ece'] > cfg.max_ece:
        reasons.append('보정 오차(ECE) %.3f > %.2f' % (r['ece'] or float('nan'), cfg.max_ece))
    return {'status': 'validated' if not reasons else 'not_validated', 'reasons': reasons}
