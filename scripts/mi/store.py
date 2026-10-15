"""SQLite archive. Append/upsert only: nothing here deletes observations, forecasts or news.

Plain SQL (no SQLite-only types beyond TEXT/REAL/INTEGER) so the schema ports to PostgreSQL.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
  market TEXT NOT NULL, date TEXT NOT NULL, close REAL NOT NULL,
  source_id TEXT NOT NULL, first_seen_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (market, date, source_id));
CREATE TABLE IF NOT EXISTS observation_revisions (
  kind TEXT NOT NULL, key TEXT NOT NULL, date TEXT NOT NULL, source_id TEXT NOT NULL,
  old_value REAL, new_value REAL, revised_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS macro (
  series_id TEXT NOT NULL, date TEXT NOT NULL, value REAL NOT NULL,
  source_id TEXT NOT NULL, first_seen_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (series_id, date));
CREATE TABLE IF NOT EXISTS forecasts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('live')),
  created_at TEXT NOT NULL, market TEXT NOT NULL, horizon INTEGER NOT NULL,
  model_name TEXT NOT NULL, model_version TEXT NOT NULL,
  input_cutoff TEXT NOT NULL, decision_time_utc TEXT NOT NULL,
  p_bull REAL NOT NULL, p_neutral REAL NOT NULL, p_bear REAL NOT NULL,
  threshold REAL NOT NULL, base_close REAL NOT NULL,
  expected_outcome_date TEXT NOT NULL, validation_status TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'evaluated')),
  outcome_date TEXT, actual_return REAL, actual_class TEXT, evaluated_at TEXT,
  provenance TEXT NOT NULL,
  UNIQUE (market, horizon, model_version, input_cutoff));
CREATE TABLE IF NOT EXISTS backtest_runs (
  run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, market TEXT NOT NULL,
  horizon INTEGER NOT NULL, config TEXT NOT NULL, summary TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS backtest_predictions (
  run_id TEXT NOT NULL, model_name TEXT NOT NULL, date TEXT NOT NULL,
  p_bull REAL NOT NULL, p_neutral REAL NOT NULL, p_bear REAL NOT NULL,
  label TEXT NOT NULL, fold INTEGER NOT NULL,
  PRIMARY KEY (run_id, model_name, date));
CREATE TABLE IF NOT EXISTS news (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL, url TEXT NOT NULL,
  published_at TEXT, collected_at TEXT NOT NULL, language TEXT NOT NULL,
  related_markets TEXT NOT NULL, category TEXT NOT NULL,
  sentiment_label TEXT, sentiment_score REAL, sentiment_method TEXT,
  importance INTEGER NOT NULL, status TEXT NOT NULL, duplicate_of TEXT,
  provider TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS briefings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, session TEXT NOT NULL,
  method TEXT NOT NULL, content TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS job_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL, started_at TEXT NOT NULL,
  finished_at TEXT, status TEXT NOT NULL, message TEXT);
"""


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


@contextmanager
def connect(path=None):
    path = path or paths.DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _upsert(conn, table, key_cols, value_col, kind, key, rows, source_id):
    """Insert new observations; on a changed value keep the newest and log the revision."""
    stamp, inserted, revised = now_utc(), 0, 0
    key_where = ' AND '.join('%s = ?' % c for c in key_cols)
    for date, value in rows:
        keys = (key, date) + ((source_id,) if 'source_id' in key_cols else ())
        old = conn.execute('SELECT %s FROM %s WHERE %s' % (value_col, table, key_where), keys).fetchone()
        if old is None:
            conn.execute('INSERT INTO %s (%s, date, %s, source_id, first_seen_at, updated_at) VALUES (?,?,?,?,?,?)'
                         % (table, key_cols[0], value_col), (key, date, value, source_id, stamp, stamp))
            inserted += 1
        elif abs(old[0] - value) > 1e-12:
            conn.execute('UPDATE %s SET %s = ?, updated_at = ? WHERE %s' % (table, value_col, key_where),
                         (value, stamp) + keys)
            conn.execute('INSERT INTO observation_revisions VALUES (?,?,?,?,?,?,?)',
                         (kind, key, date, source_id, old[0], value, stamp))
            revised += 1
    return inserted, revised


def upsert_prices(conn, market, rows, source_id):
    return _upsert(conn, 'prices', ('market', 'date', 'source_id'), 'close', 'price', market, rows, source_id)


def upsert_macro(conn, series_id, rows, source_id):
    return _upsert(conn, 'macro', ('series_id', 'date'), 'value', 'macro', series_id, rows, source_id)


def load_prices(conn, market, source_id=None):
    sql, args = 'SELECT date, close, source_id FROM prices WHERE market = ?', [market]
    if source_id:
        sql += ' AND source_id = ?'
        args.append(source_id)
    return [dict(r) for r in conn.execute(sql + ' ORDER BY date', args)]


def load_macro(conn, series_id):
    return [(r['date'], r['value']) for r in
            conn.execute('SELECT date, value FROM macro WHERE series_id = ? ORDER BY date', (series_id,))]


def macro_meta(conn):
    return {r['series_id']: dict(r) for r in conn.execute(
        'SELECT series_id, MAX(date) AS as_of, COUNT(*) AS n, MAX(updated_at) AS updated_at, source_id '
        'FROM macro GROUP BY series_id')}


@contextmanager
def job(name, path=None):
    """Record every pipeline job (status page reads this)."""
    with connect(path) as conn:
        cur = conn.execute('INSERT INTO job_runs (job, started_at, status) VALUES (?,?,?)', (name, now_utc(), 'running'))
        run_id = cur.lastrowid
    result = {'message': ''}
    try:
        yield result
    except BaseException as exc:
        with connect(path) as conn:
            conn.execute('UPDATE job_runs SET finished_at=?, status=?, message=? WHERE id=?',
                         (now_utc(), 'failed', ('%s: %s' % (type(exc).__name__, exc))[:2000], run_id))
        raise
    with connect(path) as conn:
        conn.execute('UPDATE job_runs SET finished_at=?, status=?, message=? WHERE id=?',
                     (now_utc(), result.get('status', 'ok'), str(result.get('message', ''))[:2000], run_id))


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
