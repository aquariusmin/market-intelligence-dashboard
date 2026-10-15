"""v0.6 news classification / sentiment evaluation against the user's own labels.

`export` writes data/eval/news_labels.csv with the pipeline's predictions next to empty label columns.
The user fills `label_category` / `label_sentiment`; existing labels are always kept on re-export.
`score` compares predictions with the labels and writes data/dashboard/nlp_eval.json.

Display rule fixed on 2026-10-15, before any labels exist: a method is shown as "평가 통과" only with
at least MIN_LABELLED labelled headlines and macro-F1 >= MIN_MACRO_F1; otherwise it stays "참고용".
"""
import csv
import json

from . import news, paths, store

MIN_LABELLED = 100
MIN_MACRO_F1 = 0.5
SENTIMENTS = ('positive', 'neutral', 'negative')
COLUMNS = ('id', 'published_at', 'source', 'title', 'pred_category', 'pred_sentiment', 'label_category',
           'label_sentiment')


def labels_path():
    return paths.DATA / 'eval' / 'news_labels.csv'


def read_labels(path=None):
    path = path or labels_path()
    if not path.exists():
        return []
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def export(conn, path=None):
    """Add headlines not yet in the sheet; never touch rows (and labels) already there. -> (added, total)."""
    path = path or labels_path()
    rows = read_labels(path)
    known = {r['id'] for r in rows}
    added = 0
    for r in conn.execute("SELECT id, published_at, source, title, category, sentiment_label FROM news "
                          "WHERE status != 'duplicate' ORDER BY published_at"):
        if r['id'] in known:
            continue
        rows.append({'id': r['id'], 'published_at': r['published_at'] or '', 'source': r['source'], 'title': r['title'],
                     'pred_category': r['category'], 'pred_sentiment': r['sentiment_label'] or '',
                     'label_category': '', 'label_sentiment': ''})
        added += 1
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows({k: r.get(k, '') for k in COLUMNS} for r in rows)
    path.chmod(0o600)
    return added, len(rows)


def _prf(pairs, classes):
    out, f1s = {}, []
    for c in classes:
        tp = sum(1 for p, t in pairs if p == c and t == c)
        fp = sum(1 for p, t in pairs if p == c and t != c)
        fn = sum(1 for p, t in pairs if p != c and t == c)
        prec = tp / (tp + fp) if tp + fp else None
        rec = tp / (tp + fn) if tp + fn else None
        f1 = 2 * prec * rec / (prec + rec) if prec and rec else (0.0 if tp + fn else None)
        out[c] = {'precision': prec, 'recall': rec, 'f1': f1, 'support': tp + fn}
        if f1 is not None:
            f1s.append(f1)
    return out, (sum(f1s) / len(f1s) if f1s else None)


def _task(rows, pred_key, label_key, classes):
    pairs = [(r[pred_key].strip(), r[label_key].strip()) for r in rows if r.get(label_key, '').strip()]
    bad = sorted({t for _, t in pairs if t not in classes})
    pairs = [(p, t) for p, t in pairs if t in classes]
    per_class, macro = _prf(pairs, classes)
    n = len(pairs)
    passed = n >= MIN_LABELLED and macro is not None and macro >= MIN_MACRO_F1
    return {'n': n, 'accuracy': (sum(p == t for p, t in pairs) / n) if n else None, 'macro_f1': macro,
            'per_class': per_class, 'unknown_labels': bad,
            'verdict': '평가 통과' if passed else '참고용',
            'verdict_reason': ('라벨 %d개 < %d' % (n, MIN_LABELLED) if n < MIN_LABELLED else
                               'macro-F1 %.2f %s %.2f' % (macro or 0, '≥' if passed else '<', MIN_MACRO_F1))}


def score(path=None):
    rows = read_labels(path)
    categories = [c for c, _ in news.CATEGORIES] + ['Other']
    return {'generated_at': store.now_utc(), 'labels_file': 'data/eval/news_labels.csv', 'rows': len(rows),
            'rule': '라벨 %d개 이상이고 macro-F1 ≥ %.2f일 때만 "평가 통과" (2026-10-15 사전 고정)' % (MIN_LABELLED, MIN_MACRO_F1),
            'category': dict(_task(rows, 'pred_category', 'label_category', categories), method='regex-categories-v1'),
            'sentiment': dict(_task(rows, 'pred_sentiment', 'label_sentiment', SENTIMENTS), method='lexicon-en-v2')}


def load_report():
    p = paths.DASHBOARD / 'nlp_eval.json'
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
