#!/usr/bin/env python3
"""Headless browser smoke test (dev only; needs `playwright` in .venv).

Serves a directory over http, opens every tab in REAL DATA and DEMO modes at desktop and phone widths,
and fails on console errors, page errors, horizontal overflow or missing panels. Screenshots -> --shots.

  .venv/bin/python scripts/ui_smoke.py [--root .] [--shots /tmp/shots] [--chromium PATH]
"""
import argparse
import functools
import http.server
import os
import sys
import threading
from pathlib import Path

TABS = ['Overview', 'Forecast', 'Indicators', 'News Intelligence', 'Model Performance', 'System Status']


def serve(root):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Quiet, directory=str(root))
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    p.add_argument('--shots')
    p.add_argument('--chromium', default=os.environ.get('MI_CHROMIUM'))
    p.add_argument('--expect', action='append', default=[], help='text that must appear somewhere (TAB=text)')
    args = p.parse_args()
    from playwright.sync_api import sync_playwright
    srv = serve(args.root)
    url = 'http://127.0.0.1:%d/index.html' % srv.server_address[1]
    problems, seen = [], {}
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chromium) if args.chromium else pw.chromium.launch()
        for label, viewport in [('desktop', {'width': 1440, 'height': 1000}), ('phone', {'width': 390, 'height': 844})]:
            page = browser.new_page(viewport=viewport)
            errors = []
            page.on('console', lambda m: errors.append('console.%s: %s' % (m.type, m.text)) if m.type == 'error' else None)
            page.on('pageerror', lambda e: errors.append('pageerror: %s' % e))
            page.goto(url)
            page.wait_for_function("() => !document.querySelector('[data-reload]')?.disabled", timeout=15000)
            page.wait_for_timeout(400)
            for mode in ('real', 'demo'):
                page.click('[data-mode="%s"]' % mode)
                for tab in TABS:
                    page.click('nav [data-tab="%s"]' % tab)
                    page.wait_for_timeout(150)
                    for market in ('sp', 'ks'):
                        if page.locator('#market-select').count():
                            page.select_option('#market-select', market)
                            page.wait_for_timeout(100)
                        for h in ('0', '1', '2'):
                            btn = page.locator('[data-horizon="%s"]' % h)
                            if btn.count():
                                btn.first.click()
                                page.wait_for_timeout(50)
                        text = page.inner_text('#app')
                        seen['%s|%s|%s' % (mode, tab, market)] = text
                        if len(text.strip()) < 80:
                            problems.append('%s %s %s %s: view nearly empty' % (label, mode, tab, market))
                        overflow = page.evaluate('() => document.documentElement.scrollWidth - window.innerWidth')
                        if overflow > 1:
                            problems.append('%s %s %s %s: horizontal overflow %dpx' % (label, mode, tab, market, overflow))
                    if shots:
                        page.select_option('#market-select', 'sp') if page.locator('#market-select').count() else None
                        page.screenshot(path=str(shots / ('%s-%s-%s.png' % (label, mode, tab.replace(' ', '_')))), full_page=True)
            for r in ('1M', '1Y', 'ALL'):
                page.click('[data-mode="real"]')
                page.click('nav [data-tab="Overview"]')
                if page.locator('[data-range="%s"]' % r).count():
                    page.click('[data-range="%s"]' % r)
            problems += ['%s: %s' % (label, e) for e in errors]
            page.close()
        browser.close()
    srv.shutdown()
    for exp in args.expect:
        tab, _, needle = exp.partition('=')
        if not any(needle in t for k, t in seen.items() if k.startswith('real|' + tab)):
            problems.append('expected text not found on %s: %r' % (tab, needle))
    print('checked %d views' % len(seen))
    for prob in problems:
        print('FAIL', prob)
    print('OK' if not problems else '%d problem(s)' % len(problems))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
