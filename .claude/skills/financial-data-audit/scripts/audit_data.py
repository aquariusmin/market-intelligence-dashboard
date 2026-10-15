#!/usr/bin/env python3
"""Read-only data audit for the local snapshot + SQLite archive. Never writes, never calls the network.

  .venv/bin/python .claude/skills/financial-data-audit/scripts/audit_data.py [--data DIR] [--json]

Exit 0 = no FAIL findings (WARN allowed), 1 = at least one FAIL, 2 = could not run.
"""
import argparse
import json
import math
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'scripts'))

MAX_WEEKDAY_GAP = 4      # consecutive missing weekdays tolerated (holiday clusters such as Lunar New Year/Chuseok)
STALE_DAYS = {'fed_h10': 11}  # H.10 is released weekly; everything else daily
STALE_DEFAULT = 4
JUMP_SIGMA = 8           # |daily change| / trailing 60d sd
JUMP_LOOKBACK = 504      # only the last ~2 years matter for a routine audit


def weekdays_between(a, b):
    d, n = a + timedelta(days=1), 0
    while d < b:
        n += d.weekday() < 5
        d += timedelta(days=1)
    return n


def audit_series(name, rows, today, findings, source_id=None):
    if not rows:
        findings.append(('WARN', name, '관측값 없음'))
        return
    dates = [r[0] for r in rows]
    vals = [r[1] for r in rows]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        findings.append(('FAIL', name, '날짜 역행 또는 중복'))
    bad = [d for d, v in rows if not (isinstance(v, (int, float)) and math.isfinite(v))]
    if bad:
        findings.append(('FAIL', name, '비유한 값 %d건 (예: %s)' % (len(bad), bad[0])))
    future = [d for d in dates if date.fromisoformat(d) > today]
    if future:
        findings.append(('FAIL', name, '미래 날짜 %d건 (예: %s)' % (len(future), future[0])))
    weekend = [d for d in dates if date.fromisoformat(d).weekday() >= 5]
    if weekend:
        findings.append(('WARN', name, '주말 관측 %d건 (예: %s)' % (len(weekend), weekend[0])))
    gaps = []
    for a, b in zip(dates, dates[1:]):
        n = weekdays_between(date.fromisoformat(a), date.fromisoformat(b))
        if n > MAX_WEEKDAY_GAP:
            gaps.append('%s→%s (%d평일)' % (a, b, n))
    if gaps:
        findings.append(('WARN', name, '긴 누락 구간 %d개: %s' % (len(gaps), ', '.join(gaps[:3]))))
    age = (today - date.fromisoformat(dates[-1])).days
    if age > STALE_DAYS.get(source_id, STALE_DEFAULT):
        findings.append(('WARN', name, '최근 관측 %s (%d일 경과)' % (dates[-1], age)))
    if len(vals) > 61:
        # rates (fed_h15) can sit near zero: use absolute changes; prices / FX: log returns
        if source_id == 'fed_h15' or min(vals) <= 0:
            lr = [b - a for a, b in zip(vals, vals[1:])]
        else:
            lr = [math.log(b / a) for a, b in zip(vals, vals[1:])]
        jumps = []
        for i in range(max(60, len(lr) - JUMP_LOOKBACK), len(lr)):
            w = lr[i - 60:i]
            m = sum(w) / 60
            sd = math.sqrt(sum((x - m) ** 2 for x in w) / 59) or 1e-12
            if abs(lr[i]) / sd > JUMP_SIGMA:
                jumps.append('%s (%.1fσ)' % (dates[i + 1], abs(lr[i]) / sd))
        if jumps:
            findings.append(('WARN', name, '최근 이상 변동 %d건 · 원천 확인: %s' % (len(jumps), ', '.join(jumps[-3:]))))
    findings.append(('INFO', name, '%d관측 · %s ~ %s%s' % (len(rows), dates[0], dates[-1],
                                                          ' · ' + source_id if source_id else '')))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--data', type=Path, default=ROOT / 'data')
    p.add_argument('--json', action='store_true')
    p.add_argument('--today', type=date.fromisoformat, default=date.today())
    a = p.parse_args()
    try:
        from mi import licenses
    except Exception as exc:
        print('mi 패키지 import 실패 (.venv로 실행했는지 확인): %s' % exc, file=sys.stderr)
        return 2
    findings = []
    snap_path = a.data / 'markets.json'
    if snap_path.exists():
        try:
            snap = json.loads(snap_path.read_text(encoding='utf-8'))
        except ValueError as exc:
            findings.append(('FAIL', 'markets.json', 'JSON 손상: %s' % exc))
            snap = {}
        for key, m in (snap.get('markets') or {}).items():
            rows = [(r['date'], r['close']) for r in m.get('history') or []]
            sid = {'sp': 'fred_sp500', 'nq': 'fred_nasdaq100'}.get(key, 'krx_openapi')
            audit_series('price:' + key, rows, a.today, findings, sid)
            if m.get('status') != 'ok':
                findings.append(('WARN', 'price:' + key, '상태 %s · %s' % (m.get('status'), m.get('error'))))
            if rows and m.get('as_of') != rows[-1][0]:
                findings.append(('FAIL', 'price:' + key, 'as_of와 마지막 관측일 불일치'))
    else:
        findings.append(('WARN', 'markets.json', '없음 (make ingest 필요)'))
    db = a.data / 'mi.sqlite'
    if db.exists():
        conn = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
        for (sid,) in conn.execute('SELECT DISTINCT series_id FROM macro'):
            rows = conn.execute('SELECT date, value FROM macro WHERE series_id=? ORDER BY date', (sid,)).fetchall()
            src = conn.execute('SELECT DISTINCT source_id FROM macro WHERE series_id=?', (sid,)).fetchall()
            audit_series('macro:' + sid, rows, a.today, findings, ','.join(s for (s,) in src))
            for (s,) in src:
                if not licenses.storage_allowed(s):
                    findings.append(('FAIL', 'macro:' + sid, '%s는 로컬 보관 불가 소스인데 SQLite에 있음' % s))
        for (sid,) in conn.execute('SELECT DISTINCT source_id FROM prices'):
            if not licenses.storage_allowed(sid):
                findings.append(('FAIL', 'prices', '%s는 로컬 보관 불가 소스인데 SQLite에 있음' % sid))
        rev = conn.execute('SELECT COUNT(*) FROM observation_revisions').fetchone()[0]
        findings.append(('INFO', 'sqlite', '개정 기록 %d건' % rev))
        dup = conn.execute('SELECT COUNT(*) FROM (SELECT market,horizon,model_version,input_cutoff FROM forecasts '
                           'GROUP BY 1,2,3,4 HAVING COUNT(*)>1)').fetchone()[0]
        if dup:
            findings.append(('FAIL', 'forecasts', '중복 예측 키 %d건' % dup))
        conn.close()
    else:
        findings.append(('WARN', 'mi.sqlite', '없음'))
    for sid, s in licenses.SOURCES.items():
        if s['ml_training'] not in ('allowed', 'not_allowed', 'conditional', 'unclear', 'unverified'):
            findings.append(('FAIL', 'licenses', '%s: 알 수 없는 ml_training 값' % sid))
    manifest = licenses.licensed_manifest(a.data / 'licensed' / 'manifest.json')
    for m, e in manifest.items():
        if e['manifest_errors']:
            findings.append(('FAIL', 'licensed:' + m, 'manifest 누락: %s' % ', '.join(e['manifest_errors'])))
    order = {'FAIL': 0, 'WARN': 1, 'INFO': 2}
    findings.sort(key=lambda f: (order[f[0]], f[1]))
    if a.json:
        print(json.dumps([dict(level=l, target=t, detail=d) for l, t, d in findings], ensure_ascii=False, indent=1))
    else:
        for l, t, d in findings:
            print('%-4s %-18s %s' % (l, t, d))
        print('FAIL %d · WARN %d' % (sum(f[0] == 'FAIL' for f in findings), sum(f[0] == 'WARN' for f in findings)))
    return 1 if any(f[0] == 'FAIL' for f in findings) else 0


if __name__ == '__main__':
    sys.exit(main())
