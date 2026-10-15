"""Filesystem layout. Everything lives under data/ (gitignored, never published)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get('MI_DATA_DIR', ROOT / 'data'))
MARKETS_JSON = DATA / 'markets.json'
DB = DATA / 'mi.sqlite'
DASHBOARD = DATA / 'dashboard'
LICENSED = DATA / 'licensed'
EVENTS_LOCAL = DATA / 'events.local.json'
