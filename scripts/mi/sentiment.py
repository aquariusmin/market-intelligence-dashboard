"""Headline sentiment.

Default: a small hand-written English keyword heuristic (method='lexicon-en-v1'). It is coarse and
labelled as such in the UI. Optional: ProsusAI/finbert (CC BY-NC-SA 4.0, personal non-commercial use)
when `transformers` is installed and MI_FINBERT=1. Korean headlines are not scored: no Korean
financial sentiment model with a verifiable licence was found (snunlp/KR-FinBert-SC declares none).
"""
import os
import re

POS = ['beat', 'beats', 'surge', 'surges', 'rally', 'rallies', 'gain', 'gains', 'rise', 'rises', 'record high',
       'upgrade', 'strong', 'growth', 'rebound', 'optimism', 'eases', 'cooling inflation', 'soft landing',
       'cut rates', 'rate cut', 'boost', 'improve', 'improves', 'profit jumps', 'jumps']
NEG = ['miss', 'misses', 'plunge', 'plunges', 'slump', 'slumps', 'fall', 'falls', 'drop', 'drops', 'selloff',
       'sell-off', 'downgrade', 'weak', 'recession', 'fear', 'fears', 'crisis', 'default', 'loss', 'losses',
       'tariff', 'war', 'sanction', 'fraud', 'charges', 'charged', 'layoffs', 'hot inflation', 'slowdown',
       'tumble', 'tumbles', 'warning', 'probe', 'penalty', 'volatility']
_finbert = None


def _lexicon(text):
    t = ' ' + re.sub(r'[^a-z0-9\- ]', ' ', text.lower()) + ' '
    pos = sum(t.count(' %s ' % w) for w in POS)
    neg = sum(t.count(' %s ' % w) for w in NEG)
    if pos == neg:
        return {'label': 'neutral', 'score': 0.0, 'method': 'lexicon-en-v1'}
    s = (pos - neg) / (pos + neg)
    return {'label': 'positive' if s > 0 else 'negative', 'score': round(s, 3), 'method': 'lexicon-en-v1'}


def _finbert_score(text):
    global _finbert
    if _finbert is None:
        from transformers import pipeline  # optional dependency
        _finbert = pipeline('text-classification', model='ProsusAI/finbert', top_k=None)
    probs = {d['label'].lower(): d['score'] for d in _finbert(text[:512])[0]}
    label = max(probs, key=probs.get)
    return {'label': label, 'score': round(probs.get('positive', 0) - probs.get('negative', 0), 3),
            'method': 'finbert'}


def score(text, language):
    if language != 'en':
        return {'label': None, 'score': None, 'method': 'unavailable-ko'}
    if os.environ.get('MI_FINBERT') == '1':
        try:
            return _finbert_score(text)
        except Exception:  # missing package / model download failure -> documented fallback
            pass
    return _lexicon(text)
