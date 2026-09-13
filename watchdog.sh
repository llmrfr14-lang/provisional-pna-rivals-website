#!/bin/bash
# Watchdog: keeps the Elite Division site alive on ports 12000 and 12001.
# Runs as a forever-loop; start with:  ./start_all.sh
# It only (re)starts app.py for a port when nothing answers there, so it
# survives crashes, hangs and container restarts.
#
# Each server writes its PID to /tmp/elite_<port>.pid so the watchdog can tell
# which server belongs to which port without ambiguity (port 12000 runs without
# a PORT env var, port 12001 always runs with PORT=12001).

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
KILL_TIMEOUT=8            # secs to wait for a stale server to exit before SIGKILL
RESTART_DELAY=3           # secs between kill and relaunch
CHECK_INTERVAL=10         # secs between health checks
MAX_BOUNCE=5              # max consecutive failed restarts before skipping a cycle

cd "$BASE_DIR" || exit 1

# PIDs of our app.py servers in BASE_DIR serving $1.
server_pids_for() {
  local port="$1" pid cwd env_port
  for pid in $(pgrep -f "python3 app.py"); do
    cwd="$(readlink -f /proc/$pid/cwd 2>/dev/null)" || continue
    [ "$cwd" = "$BASE_DIR" ] || continue
    env_port="$(tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | sed -n 's/^PORT=//p')"
    if { [ -z "$env_port" ] && [ "$port" = 12000 ]; } || [ "$env_port" = "$port" ]; then
      echo "$pid"
    fi
  done
}

declare -A bounce
while true; do
  for spec in 12000:server.log 12001:server2.log; do
    port="${spec%%:*}"
    logfile="${spec##*:}"
    pidfile="/tmp/elite_${port}.pid"

    if curl -fsS -o /dev/null --max-time 4 "http://127.0.0.1:${port}/" 2>/dev/null; then
      # keep the pidfile in sync with whatever server is actually serving
      current="$(server_pids_for "$port" | head -1)"
      if [ -n "$current" ]; then
        echo "$current" > "$pidfile"
      else
        rm -f "$pidfile"
      fi
      bounce["$port"]=0
      continue
    fi

    if (( "${bounce[$port]:-0}" >= MAX_BOUNCE )); then
      echo "[$(date +%F\ %T)] port ${port} down ${MAX_BOUNCE} checks in a row, skipping a cycle" >> watchdog.log
      bounce["$port"]=0
      continue
    fi

    echo "[$(date +%F\ %T)] port ${port} down, restarting..." >> watchdog.log

    # Stop leftovers: matching servers plus whatever the pidfile points to.
    pids="$(server_pids_for "$port")"
    if [ -f "$pidfile" ]; then
      pids="$pids $(cat "$pidfile")"
    fi
    pids="$(echo $pids | tr ' ' '\n' | sort -un)"
    for pid in $pids; do
      # never kill a reused PID that is not one of our servers anymore
      if [ -n "$(readlink -f /proc/$pid/cwd 2>/dev/null | grep "$BASE_DIR")" ]; then
        kill "$pid" 2>/dev/null
      fi
    done
    sleep "$RESTART_DELAY"
    # still alive? force it.
    for pid in $(server_pids_for "$port"); do
      kill -9 "$pid" 2>/dev/null
    done
    sleep 1
    rm -f "$pidfile"

    PORT="$port" nohup python3 app.py >> "$logfile" 2>&1 &
    echo $! > "$pidfile"
    bounce["$port"]=$((bounce["$port"] + 1))
  done
  sleep "$CHECK_INTERVAL"
done