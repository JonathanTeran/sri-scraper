#!/bin/bash
set -euo pipefail

# Clean stale X lock files
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

mkdir -p /app/xmls /app/screenshots /app/sessions
mkdir -p "${BROWSER_PROFILE_PATH:-/app/chrome_profile}"

python - <<'PY'
import importlib.util
import sys

if importlib.util.find_spec("nodriver") is None:
    print("ERROR: nodriver no esta instalado en el runtime")
    sys.exit(1)
PY

# Start Xvfb virtual display, then Celery worker.
Xvfb :99 -screen 0 1366x768x24 -nolisten tcp &
sleep 2

# Verify Xvfb is running
if [ -e /tmp/.X11-unix/X99 ]; then
    echo "Xvfb started on :99"
else
    echo "ERROR: Xvfb failed to start"
    exit 1
fi

export DISPLAY=:99
exec celery \
  -A tasks.celery_app worker \
  --loglevel="${CELERY_LOGLEVEL:-info}" \
  --concurrency="${CELERY_WORKER_CONCURRENCY:-3}"
