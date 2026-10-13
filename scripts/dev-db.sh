#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PG_BIN="${PG_BIN:-$(brew --prefix postgresql@18)/bin}"
DATA_DIR="$PROJECT_ROOT/.runtime/postgres"
PASSWORD_FILE="$PROJECT_ROOT/.runtime/pg-password"
if [[ "${1:-start}" == stop ]]; then
  "$PG_BIN/pg_ctl" -D "$DATA_DIR" stop -m fast
  exit
fi
[[ -f "$PASSWORD_FILE" ]] || { echo 'Run scripts/init_env.py first.'; exit 1; }
if [[ ! -f "$DATA_DIR/PG_VERSION" ]]; then
  "$PG_BIN/initdb" -D "$DATA_DIR" -U recon_dev --encoding=UTF8 --auth=scram-sha-256 --pwfile="$PASSWORD_FILE"
fi
if ! "$PG_BIN/pg_ctl" -D "$DATA_DIR" status >/dev/null 2>&1; then
  "$PG_BIN/pg_ctl" -D "$DATA_DIR" -l "$PROJECT_ROOT/.runtime/postgres.log" -o '-p 55432 -h 127.0.0.1' start -w
fi
export PGPASSWORD
PGPASSWORD="$(cat "$PASSWORD_FILE")"
trap 'unset PGPASSWORD' EXIT
if [[ "$($PG_BIN/psql -h 127.0.0.1 -p 55432 -U recon_dev -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = 'reconcile'")" != 1 ]]; then
  "$PG_BIN/createdb" -h 127.0.0.1 -p 55432 -U recon_dev reconcile
fi
echo 'Project PostgreSQL ready at 127.0.0.1:55432.'
