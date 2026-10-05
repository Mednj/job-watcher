#!/bin/sh
set -eu
# This profile belongs exclusively to this service. Clear only stale singleton links.
if [ -L /profile/SingletonLock ]; then
  lock_pid=$(readlink /profile/SingletonLock | sed 's/.*-//')
  case "$lock_pid" in
    *[!0-9]*|'') ;;
    *) if ! kill -0 "$lock_pid" 2>/dev/null; then
         rm -f /profile/SingletonLock /profile/SingletonSocket /profile/SingletonCookie
       fi ;;
  esac
fi
Xvfb :99 -screen 0 1440x900x24 -nolisten tcp &
sleep 1
fluxbox >/tmp/fluxbox.log 2>&1 &
x11vnc -display :99 -localhost -rfbport 5900 -nopw -forever -shared >/tmp/vnc.log 2>&1 &
websockify --web=/usr/share/novnc 0.0.0.0:7900 127.0.0.1:5900 >/tmp/novnc.log 2>&1 &
# Launch the browser normally before any application CDP attachment.
chromium --user-data-dir=/profile --no-sandbox --disable-dev-shm-usage --no-first-run --no-default-browser-check --remote-debugging-address=127.0.0.1 --remote-debugging-port=9223 https://www.jobteaser.com/fr/job-offers &
browser_pid=$!
trap 'kill "$browser_pid" 2>/dev/null || true' TERM INT
wait "$browser_pid"
