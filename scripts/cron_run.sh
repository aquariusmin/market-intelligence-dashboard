#!/bin/sh
# cron wrapper: load secrets from a 0600 env file (cron does not see shell exports), make sure data/ exists
# for the log, and use flock so a long job (weekly train) never overlaps the next one on the SQLite lock.
# Usage: scripts/cron_run.sh <mi_cli command> [args...]
set -eu
MI="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${MI_ENV_FILE:-$HOME/.config/mi/env}"   # e.g. a line: export KRX_API_KEY=...
umask 077   # logs, lock and SQLite files readable by the owner only
if [ -r "$ENV_FILE" ]; then
  # the file is sourced as shell code and holds keys: refuse it unless it is ours and private (mode 600)
  if [ "$(stat -c %u "$ENV_FILE")" != "$(id -u)" ]; then echo "refusing $ENV_FILE: not owned by $(id -un)" >&2; exit 1; fi
  case "$(stat -c %a "$ENV_FILE")" in *00) ;; *) echo "refusing $ENV_FILE: group/other access (chmod 600)" >&2; exit 1;; esac
  . "$ENV_FILE"
fi
mkdir -p "$MI/data"
cd "$MI"
exec flock -n data/.cron.lock .venv/bin/python scripts/mi_cli.py "$@" >> data/cron.log 2>&1
