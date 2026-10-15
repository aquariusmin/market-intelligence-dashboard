"""SQLite archive. Append/upsert only: nothing here deletes observations, forecasts or news.

Plain SQL (no SQLite-only types beyond TEXT/REAL/INTEGER) so the schema ports to PostgreSQL.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

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
CREATE TABLE IF NOT EXISTS holdout_registry (
  market TEXT NOT NULL, horizon INTEGER NOT NULL, holdout_start TEXT NOT NULL, fixed_at TEXT NOT NULL,
  PRIMARY KEY (market, horizon));
CREATE TABLE IF NOT EXISTS job_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL, started_at TEXT NOT NULL,
  finished_at TEXT, status TEXT NOT NULL, message TEXT);
"""
SCHEMA_VERSION = 2  # PRAGMA user_version; bump with an explicit migration step in _migrate()
BUSY_TIMEOUT_S = 60
STALE_JOB_HOURS = 6


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _owner_only(path):
    """Archive holds licensed provider data: readable by the owner only, like the JSON outputs (0600)."""
    for p in (path, path.with_name(path.name + '-wal'), path.with_name(path.name + '-shm')):
        try:
            if p.exists() and p.stat().st_mode & 0o077:
                p.chmod(0o600)
        except OSError:
            pass


@contextmanager
def connect(path=None):
    path = path or paths.DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S)
    _owner_only(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')  # readers (status, UI exports) never block the writer
    conn.executescript(SCHEMA)
    _migrate(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate(conn):
    """Forward-only schema migrations keyed on PRAGMA user_version. New tables come from SCHEMA
    (CREATE IF NOT EXISTS); column changes to existing tables must be added here as ALTER steps."""
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version < SCHEMA_VERSION:
        # v1 -> v2: holdout_registry table (created by SCHEMA above); no column changes.
        conn.execute('PRAGMA user_version = %d' % SCHEMA_VERSION)
        conn.commit()


def holdout_start(conn, market, horizon):
    row = conn.execute('SELECT holdout_start FROM holdout_registry WHERE market=? AND horizon=?',
                       (market, horizon)).fetchone()
    return row[0] if row else None


def fix_holdout_start(conn, market, horizon, start):
    """Record the holdout start the first time a pair is evaluated; later runs reuse it (never re-drawn)."""
    conn.execute('INSERT OR IGNORE INTO holdout_registry VALUES (?,?,?,?)', (market, horizon, start, now_utc()))


def _upsert(conn, table, key_cols, value_col, kind, key, rows, source_id):
    """Insert new observations; on a changed value keep the newest and log the revision."""
    stamp, inserted, revised = now_utc(), 0, 0
    key_where = ' AND '.join('%s = ?' % c for c in key_cols)
    for date, value in rows:
        keys = (key, date) + ((source_id,) if 'source_id' in key_cols else ())
        old = conn.execute('SELECT %s, source_id FROM %s WHERE %s' % (value_col, table, key_where), keys).fetchone()
        if old is None:
            conn.execute('INSERT INTO %s (%s, date, %s, source_id, first_seen_at, updated_at) VALUES (?,?,?,?,?,?)'
                         % (table, key_cols[0], value_col), (key, date, value, source_id, stamp, stamp))
            inserted += 1
        elif abs(old[0] - value) > 1e-12 or old[1] != source_id:
            # source_id is updated too when the key omits it (macro), so provenance follows the value.
            conn.execute('UPDATE %s SET %s = ?, source_id = ?, updated_at = ? WHERE %s' % (table, value_col, key_where),
                         (value, source_id, stamp) + keys)
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
        # A job killed by SIGKILL / power loss never reaches its UPDATE: mark such leftovers, keep the rows.
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=STALE_JOB_HOURS)).isoformat(timespec='seconds')
        conn.execute("UPDATE job_runs SET status='interrupted', finished_at=?, message='프로세스 비정상 종료 추정' "
                     "WHERE job=? AND status='running' AND started_at < ?", (now_utc(), name, cutoff))
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
