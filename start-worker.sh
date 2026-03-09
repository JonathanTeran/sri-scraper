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

if [ "${ENABLE_VNC:-0}" = "1" ] || [ "${ENABLE_NOVNC:-0}" = "1" ]; then
  VNC_PORT="${VNC_PORT:-5900}"
  X11VNC_ARGS=(
    -display :99
    -rfbport "${VNC_PORT}"
    -forever
    -shared
    -localhost
  )

  if [ -n "${VNC_PASSWORD:-}" ]; then
    x11vnc -storepasswd "${VNC_PASSWORD}" /tmp/x11vnc.pass >/dev/null
    chmod 600 /tmp/x11vnc.pass
    X11VNC_ARGS+=(-rfbauth /tmp/x11vnc.pass)
  else
    X11VNC_ARGS+=(-nopw)
  fi

  x11vnc "${X11VNC_ARGS[@]}" >/tmp/x11vnc.log 2>&1 &
  echo "x11vnc started on 127.0.0.1:${VNC_PORT}"

  if [ "${ENABLE_NOVNC:-0}" = "1" ]; then
    NOVNC_PORT="${NOVNC_PORT:-6901}"
    NOVNC_WEB_DIR="${NOVNC_WEB_DIR:-/usr/share/novnc}"
    websockify \
      --web "${NOVNC_WEB_DIR}" \
      "${NOVNC_PORT}" \
      "127.0.0.1:${VNC_PORT}" \
      >/tmp/novnc.log 2>&1 &
    echo "noVNC started on 0.0.0.0:${NOVNC_PORT}"
  fi
fi

exec celery \
  -A tasks.celery_app worker \
  --loglevel="${CELERY_LOGLEVEL:-info}" \
  --concurrency="${CELERY_WORKER_CONCURRENCY:-3}"
