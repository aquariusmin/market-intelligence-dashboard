"""News intelligence. Stores metadata only (title, source, URL, times) — never article bodies.

News analysis is an interpretation layer: it is NOT a feature of the forecasting models
(no point-in-time news archive exists to validate it).
"""
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urlsplit, urlunsplit

import providers

from . import licenses, sentiment, store

UA = 'MarketIntelligenceLocal/1.0 (personal research; contact via repository owner)'
FEEDS = {
    'fed_press_rss': {'name': 'Federal Reserve Board', 'url': 'https://www.federalreserve.gov/feeds/press_all.xml',
                      'official': True, 'markets': ['sp', 'nq', 'ks', 'kq']},
    'sec_press_rss': {'name': 'U.S. SEC', 'url': 'https://www.sec.gov/news/pressreleases.rss',
                      'official': True, 'markets': ['sp', 'nq']},
}
GDELT_QUERIES = {
    'us': ('("stock market" OR "Wall Street" OR "S&P 500" OR Nasdaq OR "Federal Reserve") sourcelang:english',
           ['sp', 'nq']),
    'kr': ('(KOSPI OR KOSDAQ OR "Bank of Korea" OR "Korean won") sourcelang:english', ['ks', 'kq']),
}

CATEGORIES = [  # first match wins; English + Korean keywords
    # English terms use word boundaries with explicit inflections only (no open-ended \w* suffixes, which
    # matched "important" -> import, "Warner" -> war). Korean 경기 is limited to economic compounds so
    # that e.g. 경기도 (the province) does not count as growth news.
    ('Monetary Policy', r'\b(fomc|federal open market|monetary policy|interest rates?|rate (cut|hike)s?|discount rate|'
                        r'policy rates?|central banks?|fed chair|powell|bank of korea|ecb)\b|기준금리|금통위|통화정책'),
    ('Inflation', r'\b(inflation|cpi|pce|consumer prices?|producer prices?|ppi)\b|물가|인플레이션'),
    ('Employment', r'\b(jobs|payrolls|unemployment|jobless|labor market|employment)\b|고용|실업'),
    ('Economic Growth', r'\b(gdp|recessions?|economic growth|retail sales|manufacturing|pmi|consumer spending)\b|'
                        r'성장률|경기\s?(침체|둔화|회복|부진|지표|하강|확장|부양|선행지수|동행지수)'),
    ('Earnings', r'\b(earnings|quarterly results|revenues?|profits?|guidance|eps)\b|실적|영업이익'),
    ('Financial Stability', r'\b(bank failures?|stress tests?|financial stability|liquidity|capital requirements?|'
                            r'supervision|supervisory|supervisors?|enforcement|fraud|charges|charged|'
                            r'settles?|settled|settlements?)\b|금융안정|부실'),
    ('Trade', r'\b(tariffs?|trade (war|deal|deficit)s?|exports?|exported|imports?|imported|sanctions?|sanctioned)\b|'
              r'관세|수출'),
    ('Geopolitics', r'\b(wars?|military|missiles?|geopolitics|geopolitical|conflicts?|elections?|north korea)\b|'
                    r'지정학|북한'),
    ('Technology', r'\b(ai|artificial intelligence|semiconductors?|chips?|nvidia|tech stocks?|software)\b|반도체'),
]
CATEGORY_WEIGHT = {'Monetary Policy': 3, 'Inflation': 3, 'Employment': 2, 'Economic Growth': 2,
                   'Financial Stability': 2, 'Earnings': 1, 'Trade': 2, 'Geopolitics': 2, 'Technology': 1}
MARKET_HINTS = [(r'\b(nasdaq|tech|semiconductor|chip|nvidia|ai)\b', ['nq']), (r'\b(s&p|wall street|dow)\b', ['sp']),
                (r'\b(kospi|samsung|sk hynix|korea|won)\b|코스피|원화', ['ks']), (r'\b(kosdaq)\b|코스닥', ['kq'])]


def norm_url(url):
    p = urlsplit(url.strip())
    return urlunsplit((p.scheme.lower(), p.netloc.lower().removeprefix('www.'), p.path.rstrip('/'), '', ''))


def norm_title(title):
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', title.lower())).strip()


def news_id(url):
    return hashlib.sha1(norm_url(url).encode()).hexdigest()[:16]


def categorize(title):
    t = title.lower()
    for name, pattern in CATEGORIES:
        if re.search(pattern, t):
            return name
    return 'Other'


def related(title, default):
    t, out = title.lower(), []
    for pattern, ms in MARKET_HINTS:
        if re.search(pattern, t):
            out += [m for m in ms if m not in out]
    return out or list(default)


def base_importance(category, official):
    """Importance as stored at ingest time: depends only on category and source, never on age."""
    return max(1, min(5, 1 + CATEGORY_WEIGHT.get(category, 0) + (1 if official else 0)))


def effective_importance(base, published_at, now):
    """Read-time importance: the stored base minus an age penalty computed against `now` (not ingest time)."""
    score = base
    if published_at:
        age = (now - datetime.fromisoformat(published_at)).total_seconds() / 3600
        score -= 1 if age > 72 else 0
    return max(1, min(5, score))


def importance(category, official, published_at, now):
    return effective_importance(base_importance(category, official), published_at, now)


def parse_rss(text, source_id):
    root = ET.fromstring(text)
    out = []
    for item in root.iter('item'):
        title = (item.findtext('title') or '').strip()
        link = (item.findtext('link') or '').strip()
        pub = item.findtext('pubDate')
        if not title or not link.startswith('http'):
            continue
        try:
            published = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat(timespec='seconds') if pub else None
        except (TypeError, ValueError):
            published = None
        out.append({'title': title, 'url': link, 'published_at': published, 'provider': source_id,
                    'source': FEEDS[source_id]['name'], 'language': 'en', 'official': True,
                    'default_markets': FEEDS[source_id]['markets']})
    return out


def parse_gdelt(text, default_markets):
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError('GDELT 응답이 JSON이 아님 (요청 한도 가능)') from None
    out = []
    for a in data.get('articles') or []:
        if not a.get('url') or not a.get('title'):
            continue
        # GDELT `seendate` is when GDELT's crawler first saw the article (crawl time), not the publisher's
        # timestamp. It is stored in published_at (schema unchanged) and is an upper bound on publication time.
        seen = a.get('seendate')  # 20261015T120000Z
        published = (datetime.strptime(seen, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).isoformat(timespec='seconds')
                     if seen else None)
        lang = 'ko' if a.get('language', '').lower().startswith('korean') else 'en'
        out.append({'title': a['title'].strip(), 'url': a['url'], 'published_at': published, 'provider': 'gdelt_doc',
                    'source': a.get('domain') or urlsplit(a['url']).netloc, 'language': lang, 'official': False,
                    'default_markets': default_markets})
    return out


def dedupe(items, existing_titles=None, window_hours=48, threshold=0.8):
    """Exact URL dedupe + near-duplicate titles (token Jaccard >= threshold within window_hours)."""
    kept, seen_urls = [], set()
    pool = list(existing_titles or [])
    for it in sorted(items, key=lambda x: x.get('published_at') or ''):
        u = norm_url(it['url'])
        if u in seen_urls:
            continue
        seen_urls.add(u)
        tokens = set(norm_title(it['title']).split())
        dup = None
        for other_id, other_tokens, other_time in pool:
            if not tokens or not other_tokens:
                continue
            j = len(tokens & other_tokens) / len(tokens | other_tokens)
            if j >= threshold and _close_in_time(it.get('published_at'), other_time, window_hours):
                dup = other_id
                break
        it['duplicate_of'] = dup
        kept.append(it)
        if dup is None:
            pool.append((news_id(it['url']), tokens, it.get('published_at')))
    return kept


def _close_in_time(a, b, hours):
    if not a or not b:
        return True
    return abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds()) <= hours * 3600


def collect(conn, request=None, sleep=time.sleep, now=None, use_gdelt=True, log=print):
    now = now or datetime.now(timezone.utc)
    request = request or (lambda url: providers.request_text(url, {'User-Agent': UA}))
    items, report = [], {}
    for sid, feed in FEEDS.items():
        try:
            got = parse_rss(request(feed['url']), sid)
            items += got
            report[sid] = {'status': 'ok', 'items': len(got), 'error': None}
        except Exception as exc:
            report[sid] = {'status': 'failed', 'items': 0, 'error': str(exc)[:300]}
        log('%s: %s' % (sid, report[sid]))
    if use_gdelt:
        got_total, err = 0, None
        for i, (key, (query, markets)) in enumerate(GDELT_QUERIES.items()):
            if i:
                sleep(6)  # GDELT: max one request per 5 seconds
            url = 'https://api.gdeltproject.org/api/v2/doc/doc?' + urlencode(
                {'query': query, 'mode': 'artlist', 'format': 'json', 'maxrecords': 40, 'timespan': '2d',
                 'sort': 'datedesc'})
            try:
                got = parse_gdelt(request(url), markets)
                items += got
                got_total += len(got)
            except Exception as exc:
                err = str(exc)[:300]
                if 'HTTP 429' in str(exc):  # rate-limited: do not issue the remaining queries in this run
                    err += ' · 남은 GDELT 쿼리 생략'
                    break
        report['gdelt_doc'] = {'status': 'ok' if err is None else ('partial' if got_total else 'failed'),
                               'items': got_total, 'error': err}
        log('gdelt_doc: %s' % report['gdelt_doc'])
    existing = [(r['id'], set(norm_title(r['title']).split()), r['published_at'])
                for r in conn.execute("SELECT id, title, published_at FROM news WHERE duplicate_of IS NULL "
                                      "AND collected_at >= ?", ((now - timedelta(days=4)).isoformat(),))]
    known = {r['id'] for r in conn.execute('SELECT id FROM news')}
    fresh = [it for it in items if news_id(it['url']) not in known]
    added, skipped = 0, {}
    for it in dedupe(fresh, existing):
        if not licenses.storage_allowed(it['provider']):  # unverified storage permission -> never archived
            skipped[it['provider']] = skipped.get(it['provider'], 0) + 1
            continue
        cat = categorize(it['title'])
        sent = sentiment.score(it['title'], it['language'])
        conn.execute('INSERT OR IGNORE INTO news VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (news_id(it['url']), it['title'][:500], it['source'][:200], it['url'][:1000],
                      it['published_at'], now.isoformat(timespec='seconds'), it['language'],
                      store.dumps(related(it['title'], it['default_markets'])), cat, sent['label'], sent['score'],
                      sent['method'], base_importance(cat, it['official']),  # age penalty applied on read
                      'duplicate' if it['duplicate_of'] else 'processed', it['duplicate_of'], it['provider']))
        added += 1
    report['added'] = added
    report['skipped_unlicensed'] = sum(skipped.values())
    for provider, n in skipped.items():
        entry = report.setdefault(provider, {'status': 'ok', 'items': 0, 'error': None})
        entry.update(storage='skipped', skipped=n, reason='licenses.storage_allowed 불통과 · 저장하지 않음')
    return report


def recent(conn, days=14, limit=200, now=None):
    now = now or datetime.now(timezone.utc)
    rows = conn.execute("SELECT * FROM news WHERE duplicate_of IS NULL AND COALESCE(published_at, collected_at) >= ? "
                        "ORDER BY COALESCE(published_at, collected_at) DESC LIMIT ?",
                        ((now - timedelta(days=days)).isoformat(), limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d['related_markets'] = json.loads(d['related_markets'])
        # Rows stored before read-time aging may already include an ingest-time age penalty; they are not rewritten.
        d['base_importance'] = d['importance']
        d['importance'] = effective_importance(d['importance'], d['published_at'], now)
        out.append(d)
    return out
