#!/usr/bin/env bash
# Event Sync Service -- single-command start (macOS / Linux).
#
#   ./start.sh
#
# Creates the Python virtualenv and installs both dependency sets on first run,
# then starts the API and the frontend together. Ctrl+C stops both.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV_PY="$BACKEND/.venv/bin/python"

info() { printf '\033[36m==> %s\033[0m\n' "$1"; }
die()  { printf '\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

# --- Prerequisites --------------------------------------------------------
command -v python3 >/dev/null 2>&1 || die "Missing 'python3'. Python 3.11+ is required."
command -v npm     >/dev/null 2>&1 || die "Missing 'npm'. Node 18+ is required."

# --- API port check -------------------------------------------------------
# If 8000 is taken, uvicorn exits but the UI still starts and proxies /api to
# whatever owns the port -- so stop here with a clear message instead. The
# probe binds the way uvicorn does (SO_REUSEADDR) to avoid TIME_WAIT false alarms.
API_PORT=8000
port_status=0
python3 - "$API_PORT" 2>/dev/null <<'PY' || port_status=$?
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(3)  # only a failed bind means "busy"; any other failure falls through
s.close()
PY
if [ "$port_status" -eq 3 ]; then
  pid="" name=""
  if command -v lsof >/dev/null 2>&1; then
    read -r pid name < <(lsof -nP -iTCP:"$API_PORT" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $2, $1}') || true
  fi
  if [ -n "$pid" ]; then
    die "Port $API_PORT is already in use by PID $pid ($name), so the API cannot start. Stop it with 'kill $pid' and re-run ./start.sh."
  fi
  die "Port $API_PORT is already in use, so the API cannot start. Find the owner with 'lsof -iTCP:$API_PORT -sTCP:LISTEN', stop it, and re-run."
fi

# --- Backend setup --------------------------------------------------------
if [ ! -x "$VENV_PY" ]; then
  info "Creating Python virtualenv"
  python3 -m venv "$BACKEND/.venv"
  info "Installing backend dependencies"
  "$VENV_PY" -m pip install --quiet --upgrade pip
  "$VENV_PY" -m pip install --quiet -r "$BACKEND/requirements.txt"
fi

# --- Frontend setup -------------------------------------------------------
if [ ! -d "$FRONTEND/node_modules" ]; then
  info "Installing frontend dependencies (first run only, may take a minute)"
  (cd "$FRONTEND" && npm install --no-audit --no-fund --silent)
fi

# --- Run both -------------------------------------------------------------
cleanup() {
  if [ -n "${API_PID:-}" ] && kill -0 "$API_PID" 2>/dev/null; then
    info "Stopping API"
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

info "Starting API on http://127.0.0.1:$API_PORT  (docs at /docs)"
# 'exec' so the subshell is replaced by uvicorn itself -- otherwise $! is the
# subshell and cleanup would leave an orphaned server holding port 8000.
(cd "$BACKEND" && exec "$VENV_PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT") &
API_PID=$!

info "Starting UI -- open the 'Local:' URL Vite prints below (usually http://localhost:5173)"
info "Ctrl+C stops both processes."
(cd "$FRONTEND" && npm run dev)
