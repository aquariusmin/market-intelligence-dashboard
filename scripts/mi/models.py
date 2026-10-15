"""Forecast models (3 classes: 0 bearish, 1 neutral, 2 bullish) and probability calibrators.

Every model exposes fit(X, y) and predict_proba(X) -> (n, 3) rows summing to 1.
Imputation / scaling live inside the model so they are fitted on the training slice only.
"""
import warnings

import numpy as np
from scipy.optimize import minimize_scalar
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

K = 3
EPS = 1e-6


def _full(proba, classes):
    out = np.full((proba.shape[0], K), EPS)
    out[:, np.asarray(classes, int)] = proba
    return out / out.sum(1, keepdims=True)


class ClassFrequency:
    name = 'class_frequency'
    family = 'baseline'

    def __init__(self, alpha=1.0):
        self.alpha = alpha

    def fit(self, X, y):
        counts = np.bincount(np.asarray(y, int), minlength=K) + self.alpha
        self.p_ = counts / counts.sum()
        return self

    def predict_proba(self, X):
        return np.tile(self.p_, (len(X), 1))


class MomentumRule:
    """Sign of the 20-day return; probabilities = training-slice class frequencies given that sign."""
    name = 'momentum_rule'
    family = 'baseline'

    def __init__(self, feature='ret20', alpha=1.0):
        self.feature, self.alpha = feature, alpha

    def _bucket(self, X):
        v = X[self.feature].to_numpy(float)
        return np.where(np.isnan(v), 1, np.where(v > 0, 2, 0))

    def fit(self, X, y):
        b, y = self._bucket(X), np.asarray(y, int)
        self.table_ = np.vstack([np.bincount(y[b == i], minlength=K) + self.alpha for i in range(3)])
        self.table_ = self.table_ / self.table_.sum(1, keepdims=True)
        return self

    def predict_proba(self, X):
        return self.table_[self._bucket(X)]


class LogReg:
    name = 'logistic_regression'
    family = 'baseline'

    def __init__(self, C=0.1):
        self.C = C

    def fit(self, X, y):
        self.pipe_ = make_pipeline(SimpleImputer(strategy='median', keep_empty_features=True), StandardScaler(),
                                   LogisticRegression(C=self.C, max_iter=3000))
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            self.pipe_.fit(X.to_numpy(float), np.asarray(y, int))
        return self

    def predict_proba(self, X):
        return _full(self.pipe_.predict_proba(X.to_numpy(float)), self.pipe_.classes_)


class LightGBM:
    name = 'lightgbm'
    family = 'ml'

    def __init__(self, num_leaves=7, n_estimators=200, learning_rate=0.03, min_child_samples=50):
        self.params = dict(num_leaves=num_leaves, n_estimators=n_estimators, learning_rate=learning_rate,
                           min_child_samples=min_child_samples)

    def fit(self, X, y):
        import lightgbm as lgb
        self.model_ = lgb.LGBMClassifier(objective='multiclass', subsample=0.8, subsample_freq=1,
                                         colsample_bytree=0.8, reg_lambda=1.0, verbose=-1, n_jobs=2,
                                         random_state=0, deterministic=True, **self.params)
        self.model_.fit(X.to_numpy(float), np.asarray(y, int))
        return self

    def predict_proba(self, X):
        return _full(self.model_.predict_proba(X.to_numpy(float)), self.model_.classes_)

    def importance(self, columns):
        gain = self.model_.booster_.feature_importance('gain')
        total = gain.sum() or 1.0
        return sorted(({'feature': c, 'gain_share': float(g / total)} for c, g in zip(columns, gain)),
                      key=lambda r: -r['gain_share'])


GRID = {
    'class_frequency': [{}],
    'momentum_rule': [{}],
    'logistic_regression': [{'C': 0.01}, {'C': 0.1}, {'C': 1.0}],
    'lightgbm': [{'num_leaves': 4, 'n_estimators': 150}, {'num_leaves': 7, 'n_estimators': 300},
                 {'num_leaves': 15, 'n_estimators': 150}],
}
FACTORY = {'class_frequency': ClassFrequency, 'momentum_rule': MomentumRule, 'logistic_regression': LogReg,
           'lightgbm': LightGBM}
CALIBRATED = {'logistic_regression', 'lightgbm'}  # baselines are frequency tables already


# ---------- calibration: fitted on a chronologically later slice than the model ----------

class Identity:
    name = 'none'

    def fit(self, p, y):
        return self

    def transform(self, p):
        return p


class Temperature:
    """Multiclass temperature scaling on log-probabilities."""
    name = 'temperature'

    def fit(self, p, y):
        logp, y = np.log(np.clip(p, EPS, 1)), np.asarray(y, int)

        def nll(t):
            z = logp / t
            z = z - z.max(1, keepdims=True)
            q = np.exp(z) / np.exp(z).sum(1, keepdims=True)
            return -np.log(np.clip(q[np.arange(len(y)), y], EPS, 1)).mean()
        self.t_ = minimize_scalar(nll, bounds=(0.25, 10), method='bounded').x
        return self

    def transform(self, p):
        z = np.log(np.clip(p, EPS, 1)) / self.t_
        z = z - z.max(1, keepdims=True)
        return np.exp(z) / np.exp(z).sum(1, keepdims=True)


class OneVsRest:
    """Per-class sigmoid (Platt) or isotonic map, then row renormalisation."""

    def __init__(self, method):
        self.name = method

    def fit(self, p, y):
        y = np.asarray(y, int)
        self.maps_ = []
        for k in range(K):
            t = (y == k).astype(int)
            if t.min() == t.max():
                self.maps_.append(None)
                continue
            if self.name == 'isotonic':
                m = IsotonicRegression(out_of_bounds='clip', y_min=0, y_max=1).fit(p[:, k], t)
            else:
                logit = np.log(np.clip(p[:, k], EPS, 1 - EPS) / np.clip(1 - p[:, k], EPS, 1))
                m = LogisticRegression(C=1e4, max_iter=1000).fit(logit[:, None], t)
            self.maps_.append(m)
        return self

    def transform(self, p):
        cols = []
        for k, m in enumerate(self.maps_):
            if m is None:
                cols.append(p[:, k])
            elif self.name == 'isotonic':
                cols.append(m.predict(p[:, k]))
            else:
                logit = np.log(np.clip(p[:, k], EPS, 1 - EPS) / np.clip(1 - p[:, k], EPS, 1))
                cols.append(m.predict_proba(logit[:, None])[:, 1])
        q = np.clip(np.column_stack(cols), EPS, None)
        return q / q.sum(1, keepdims=True)


CALIBRATORS = {'none': Identity, 'temperature': Temperature,
               'sigmoid': lambda: OneVsRest('sigmoid'), 'isotonic': lambda: OneVsRest('isotonic')}
