#!/bin/bash
# Bounce the Elite Division site: (re)start servers + watchdog in one shot.
# Safe to re-run at any time — it kills stale instances first.
#
#   Main site (work-1): port 12000  -> server.log   -> no PORT env (app default)
#   2nd site  (work-2): port 12001  -> server2.log  -> PORT=12001

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR" || exit 1

stop_all() {
  echo ">> stopping existing instances..."
  pkill -f "watchdog.sh" 2>/dev/null
  for pid in $(pgrep -f "python3 app.py"); do
    [ "$(readlink -f /proc/$pid/cwd 2>/dev/null)" = "$BASE_DIR" ] || continue
    kill "$pid" 2>/dev/null
  done
  sleep 2
  for pid in $(pgrep -f "python3 app.py"); do
    [ "$(readlink -f /proc/$pid/cwd 2>/dev/null)" = "$BASE_DIR" ] || continue
    kill -9 "$pid" 2>/dev/null
  done
  rm -f /tmp/elite_12000.pid /tmp/elite_12001.pid
  sleep 1
}

echo "== Elite Division restart =="
stop_all

echo ">> starting server on 12000 (work-1)..."
PORT=12000 nohup python3 app.py >> server.log 2>&1 &
echo $! > /tmp/elite_12000.pid

echo ">> starting server on 12001 (work-2)..."
PORT=12001 nohup python3 app.py >> server2.log 2>&1 &
echo $! > /tmp/elite_12001.pid

echo ">> starting watchdog..."
nohup ./watchdog.sh >> watchdog.log 2>&1 &

sleep 4
echo ">> health check:"
for port in 12000 12001; do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://127.0.0.1:${port}/")
  echo "   port ${port} -> HTTP ${code}"
done