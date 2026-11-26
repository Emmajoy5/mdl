#!/usr/bin/env bash
# Start the Microbial Digital Library lab web app.
#   - uses gunicorn if it is installed (one worker, several threads: the login
#     rate limiter and the chat helper live in that one process), else app.py,
#     which uses waitress if installed, else Flask's development server.
#   - for HTTPS set MDL_SSL_CERT and MDL_SSL_KEY (see README "HTTPS on a lab network").
set -e
cd "$(dirname "$0")"
PORT="${MDL_PORT:-5000}"
if command -v gunicorn >/dev/null 2>&1 && [ -z "$MDL_SSL_CERT" ]; then
  python3 -c "from app import init_db_if_needed, _print_startup_diagnostics; init_db_if_needed(); _print_startup_diagnostics()"
  exec gunicorn --workers 1 --threads 8 --bind "${MDL_HOST:-0.0.0.0}:${PORT}" wsgi:app
fi
exec python3 app.py
