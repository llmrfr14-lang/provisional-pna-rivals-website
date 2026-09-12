#!/usr/bin/env python3
"""
Elite Division — provisional league site.
Round robin group stage (2 groups x 4 teams), 3 pts per win, top 2 of each group
go to semis, winners meet in the final. Anybody with the link can report results.
Uses only the Python standard library (no dependencies).

Endpoints:
  GET  /            -> site (static/index.html)
  GET  /api/state   -> full state (schedule, standings, bracket, results)
  POST /api/report  -> {"match_id": "...", "winner": "team1"|"team2", "reporter": "..."}
  POST /api/undo    -> {"match_id": "..."} (clears a reported result)

Storage: results are persisted wherever possible:
  - If SUPABASE_URL + SUPABASE_SERVICE_KEY are set, they sync to the Supabase
    table `elite_state` (see supabase_setup.sql) via its REST API.
  - Otherwise (and as a local mirror) they live in data.json.
Standings and brackets are always computed on the fly — never stored.
"""
import json
import os
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone
from functools import cmp_to_key
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DATA_PATH = os.path.join(BASE_DIR, "data.json")
STATIC_DIR = os.path.join(BASE_DIR, "static")
PORT = int(os.environ.get("PORT", "12000"))

lock = threading.Lock()

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
SUPABASE_KEY = (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()

# Optional .env support (no external deps) so creds survive server restarts.
_ENV_PATH = os.path.join(BASE_DIR, ".env")
if (not SUPABASE_URL or not SUPABASE_KEY) and os.path.exists(_ENV_PATH):
    with open(_ENV_PATH, "r", encoding="utf-8") as _fh:
        for _line in _fh:
            _line = _line.strip()
            if not _line or "=" not in _line or _line.startswith("#"):
                continue
            _k, _v = _line.split("=", 1)
            if _k == "SUPABASE_URL" and not SUPABASE_URL:
                SUPABASE_URL = _v.strip().strip('"').strip("'").rstrip("/")
            elif _k == "SUPABASE_SERVICE_KEY" and not SUPABASE_KEY:
                SUPABASE_KEY = _v.strip().strip('"').strip("'")

SUPABASE_TABLE = "elite_state"

ADMIN_CODE = (os.environ.get("ADMIN_CODE") or "").strip()
_ENV_PATH2 = os.path.join(BASE_DIR, ".env")
if not ADMIN_CODE and os.path.exists(_ENV_PATH2):
    with open(_ENV_PATH2, "r", encoding="utf-8") as _fh:
        for _line in _fh:
            _line = _line.strip()
            if _line.startswith("ADMIN_CODE="):
                ADMIN_CODE = _line.split("=", 1)[1].strip().strip('"').strip("'")


# ----------------------------------------------------------------- storage

def _supabase_endpoint():
    return SUPABASE_URL + "/rest/v1/" + SUPABASE_TABLE


def _http_json(url, method="GET", payload=None, headers=None):
    req_headers = {
        "Content-Type": "application/json",
        "apikey": SUPABASE_KEY,
        "Authorization": "Bearer " + SUPABASE_KEY,
    }
    if headers:
        req_headers.update(headers)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def supabase_load():
    """Returns stored state dict or None if Supabase not configured/unreachable."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        rows = _http_json(_supabase_endpoint() + "?id=eq.state&select=data")
        if rows and rows[0].get("data"):
            return rows[0]["data"]
    except Exception:
        pass
    return None


def supabase_save(data):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        _http_json(
            _supabase_endpoint() + "?id=eq.state",
            method="POST",
            payload=[{"id": "state", "data": data,
                      "updated_at": now_iso()}],
            headers={"Prefer": "resolution=merge-duplicates"},
        )
        return True
    except Exception:
        return False


def normalize_status(data):
    """Backfill the `status` field on older stored data.

    Legacy entries may lack `status`. Once a match has a winner, it used to be
    auto-final; treat those as pre-approved so nothing is lost.
    """
    for bucket in (data.get("matches") or [], data.get("playoffs") or []):
        for m in bucket:
            if m.get("winner"):
                m.setdefault("status", "approved")
            else:
                m.setdefault("status", None)


def load_data():
    """Load state, always with a complete schedule: prefer Supabase, else local."""
    remote = None
    if SUPABASE_URL and SUPABASE_KEY:
        remote = supabase_load()
    data = None
    if remote is not None:
        data = remote
    elif os.path.exists(DATA_PATH):
        with open(DATA_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    if data is None:
        data = {"matches": [], "playoffs": []}
    try:
        config = load_config()
        normalize_status(data)
        ensure_schedule(config, data)
        fill_playoff_teams(config, data)
        # persist any initialization so future loads see it
        save_data(data)
    except Exception:
        pass
    return data


def save_data(data):
    """Persist: write to Supabase if configured AND mirror to local data.json."""
    if SUPABASE_URL and SUPABASE_KEY:
        supabase_save(data)
    tmp = DATA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_PATH)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def round_robin(teams):
    """Circle-method round robin -> list of rounds; each round is [(home, away), ...]."""
    ts = list(teams)
    if len(ts) % 2:
        ts.append(None)  # bye
    n = len(ts)
    rounds = []
    rotated = ts[1:]
    for _ in range(n - 1):
        arr = [ts[0]] + rotated
        pairs = []
        for i in range(n // 2):
            a, b = arr[i], arr[n - 1 - i]
            if a is not None and b is not None:
                pairs.append((a, b))
        rounds.append(pairs)
        rotated = [rotated[-1]] + rotated[:-1]
    return rounds


def build_schedule(config):
    """One jornada (matchday) per round: every team plays once per jornada -> no clashes."""
    matches = []
    counter = 0
    for gname, teams in config["groups"].items():
        rounds = round_robin(teams)
        for r, pairs in enumerate(rounds, start=1):
            for home, away in pairs:
                counter += 1
                matches.append({
                    "id": "m%d" % counter,
                    "group": gname,
                    "matchday": r,
                    "team1": home,
                    "team2": away,
                    "winner": None,
                    "reported_by": None,
                    "reported_at": None,
                    "status": None,
                })
    return matches


def init_playoffs(config):
    groups = [g for g in config["groups"] if config["groups"][g]]
    gA, gB = groups[0], groups[1]
    return [
        {"id": "sf1", "stage": "Semifinal 1", "team1": None, "team2": None,
         "winner": None, "reported_by": None, "reported_at": None, "status": None,
         "source1": {"group": gA, "pos": 1}, "source2": {"group": gB, "pos": 2}},
        {"id": "sf2", "stage": "Semifinal 2", "team1": None, "team2": None,
         "winner": None, "reported_by": None, "reported_at": None, "status": None,
         "source1": {"group": gB, "pos": 1}, "source2": {"group": gA, "pos": 2}},
        {"id": "final", "stage": "Final", "team1": None, "team2": None,
         "winner": None, "reported_by": None, "reported_at": None, "status": None,
         "source1": {"match": "sf1"}, "source2": {"match": "sf2"}},
    ]


def ensure_schedule(config, data):
    """Regenerate the schedule if team names changed on disk, keeping results."""
    schedule = build_schedule(config)
    existing = data.get("matches") or []
    sig = [(m["id"], m["group"], m["matchday"], m["team1"], m["team2"]) for m in schedule]
    exsig = [(m["id"], m["group"], m["matchday"], m["team1"], m["team2"]) for m in existing]
    if sig == exsig:
        # preserve stored winner/reporter metadata on the regenerated schedule
        store = {m["id"]: m for m in existing}
        for m in schedule:
            s = store.get(m["id"])
            if s:
                m["winner"] = s.get("winner")
                m["reported_by"] = s.get("reported_by")
                m["reported_at"] = s.get("reported_at")
                m["status"] = s.get("status")
        data["matches"] = schedule
        return
    reported = {}
    for m in existing:
        if m.get("winner"):
            reported[(m["group"], m["team1"], m["team2"])] = m["winner"]
    for m in schedule:
        key = (m["group"], m["team1"], m["team2"])
        rev = (m["group"], m["team2"], m["team1"])
        if key in reported:
            m["winner"] = reported[key]
        elif rev in reported:
            m["winner"] = "team2" if reported[rev] == "team1" else "team1"
    data["matches"] = schedule
    data["playoffs"] = init_playoffs(config)


def compute_standings(config, matches):
    groups = config["groups"]
    stats = {g: {t: {"played": 0, "wins": 0, "losses": 0, "points": 0} for t in teams}
             for g, teams in groups.items()}
    h2h = {}
    for m in matches:
        if not m.get("winner") or m.get("status") != "approved" or m["group"] not in stats:
            continue
        t1, t2 = m["team1"], m["team2"]
        if t1 not in stats[m["group"]] or t2 not in stats[m["group"]]:
            continue
        winner = t1 if m["winner"] == "team1" else t2
        loser = t2 if m["winner"] == "team1" else t1
        stats[m["group"]][t1]["played"] += 1
        stats[m["group"]][t2]["played"] += 1
        stats[m["group"]][winner]["wins"] += 1
        stats[m["group"]][winner]["points"] += config.get("points_per_win", 3)
        stats[m["group"]][loser]["losses"] += 1
        h2h[tuple(sorted((t1, t2)))] = winner

    def rank_cmp(a, b, g):
        sa, sb = stats[g][a], stats[g][b]
        if sa["points"] != sb["points"]:
            return sb["points"] - sa["points"]
        if sa["wins"] != sb["wins"]:
            return sb["wins"] - sa["wins"]
        if (a, b) in h2h and h2h[(a, b)] == a:
            return -1
        if (b, a) in h2h and h2h[(b, a)] == b:
            return -1
        if a.lower() < b.lower():
            return -1
        if a.lower() > b.lower():
            return 1
        return 0

    result = {}
    for g, teams in groups.items():
        ranked = sorted(teams, key=cmp_to_key(lambda a, b: rank_cmp(a, b, g)))
        result[g] = [{"rank": i, "team": t, **stats[g][t]} for i, t in enumerate(ranked, 1)]
    return result


def compute_bracket(config, matches, playoffs):
    """Resolve bracket slots from current standings and reported playoff winners."""
    standings = compute_standings(config, matches)
    winners = {p["id"]: p for p in playoffs}
    bracket = []
    for slot in playoffs:
        item = dict(slot)
        for side in ("source1", "source2"):
            src = slot.get(side) or {}
            if "group" in src:
                rows = standings.get(src["group"], [])
                if len(rows) >= src["pos"]:
                    item["team1" if side == "source1" else "team2"] = rows[src["pos"] - 1]["team"]
            elif "match" in src:
                parent = winners.get(src["match"])
                if parent and parent.get("winner") and parent.get("status") == "approved" \
                        and parent["team1"] and parent["team2"]:
                    winner = parent["team1"] if parent["winner"] == "team1" else parent["team2"]
                    item["team1" if side == "source1" else "team2"] = winner
        bracket.append(item)
    return bracket


def fill_playoff_teams(config, data):
    """Persist the currently-qualified teams into the stored playoff slots."""
    bracket = compute_bracket(config, data["matches"], data["playoffs"])
    bmap = {b["id"]: b for b in bracket}
    for slot in data["playoffs"]:
        b = bmap[slot["id"]]
        slot["team1"], slot["team2"] = b["team1"], b["team2"]


def build_state():
    config = load_config()
    data = load_data()
    ensure_schedule(config, data)
    fill_playoff_teams(config, data)
    save_data(data)
    standings = compute_standings(config, data["matches"])
    bracket = compute_bracket(config, data["matches"], data["playoffs"])
    group_complete = {}
    pending = []
    for g in config["groups"]:
        gms = [m for m in data["matches"] if m["group"] == g]
        group_complete[g] = bool(gms) and all(
            m.get("winner") and m.get("status") == "approved" for m in gms)
    for m in data["matches"] + data["playoffs"]:
        if m.get("winner") and m.get("status") == "pending":
            pending.append(m["id"])
    return {
        "config": {
            "league": config.get("league", "Elite Division"),
            "groups": config["groups"],
            "points_per_win": config.get("points_per_win", 3),
            "rosters": config.get("rosters", {}),
        },
        "matches": data["matches"],
        "standings": standings,
        "playoffs": bracket,
        "group_complete": group_complete,
        "pending": pending,
    }


def find_slot(data, match_id):
    for m in data["matches"]:
        if m["id"] == match_id:
            return m
    for p in data["playoffs"]:
        if p["id"] == match_id:
            return p
    return None


def report_result(body):
    config = load_config()
    data = load_data()
    ensure_schedule(config, data)
    fill_playoff_teams(config, data)
    match_id = body.get("match_id")
    slot = find_slot(data, match_id)
    if slot is None:
        raise ValueError("partido no encontrado")
    if not slot["team1"] or not slot["team2"]:
        raise ValueError("los equipos de este partido aún no están definidos")
    winner = body.get("winner")
    if winner not in ("team1", "team2"):
        raise ValueError("winner debe ser team1 o team2")
    slot["winner"] = winner
    slot["status"] = "pending"
    slot["reported_by"] = (body.get("reporter") or "").strip() or "Anónimo"
    slot["reported_at"] = now_iso()
    save_data(data)
    return build_state()


def undo_result(body):
    config = load_config()
    data = load_data()
    ensure_schedule(config, data)
    slot = find_slot(data, body.get("match_id"))
    if slot is None:
        raise ValueError("partido no encontrado")
    slot["winner"] = None
    slot["status"] = None
    slot["reported_by"] = None
    slot["reported_at"] = None
    save_data(data)
    return build_state()


def _check_admin(body):
    code = (body.get("admin_code") or "").strip()
    if not ADMIN_CODE:
        raise ValueError("código admin no configurado en el servidor")
    if code != ADMIN_CODE:
        raise ValueError("código admin incorrecto")


def verify_admin(body):
    _check_admin(body)
    return {"ok": True}


def approve_result(body):
    config = load_config()
    data = load_data()
    ensure_schedule(config, data)
    _check_admin(body)
    slot = find_slot(data, body.get("match_id"))
    if slot is None:
        raise ValueError("partido no encontrado")
    if not slot.get("winner"):
        raise ValueError("ese partido no tiene un resultado por aprobar")
    slot["status"] = "approved"
    slot["approved_at"] = now_iso()
    save_data(data)
    return build_state()


def reject_result(body):
    config = load_config()
    data = load_data()
    ensure_schedule(config, data)
    _check_admin(body)
    slot = find_slot(data, body.get("match_id"))
    if slot is None:
        raise ValueError("partido no encontrado")
    if not slot.get("winner"):
        raise ValueError("ese partido no tiene un resultado por rechazar")
    slot["winner"] = None
    slot["status"] = None
    slot["reported_by"] = None
    slot["reported_at"] = None
    slot.pop("approved_at", None)
    save_data(data)
    return build_state()


# ------------------------------------------------------------------ server

class Handler(BaseHTTPRequestHandler):
    server_version = "EliteDivision/1.0"

    def log_message(self, fmt, *args):
        return

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path):
        with open(path, "rb") as fh:
            body = fh.read()
        ctype = "text/html; charset=utf-8"
        if path.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif path.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                self._send_file(os.path.join(STATIC_DIR, "index.html"))
            elif path == "/api/state":
                with lock:
                    self._send_json(build_state())
            elif path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        routes = {
            "/api/report": report_result,
            "/api/undo": undo_result,
            "/api/admin/verify": verify_admin,
            "/api/admin/approve": approve_result,
            "/api/admin/reject": reject_result,
        }
        if path not in routes:
            self._send_json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw) if raw else {}
        except ValueError:
            self._send_json({"error": "invalid JSON"}, 400)
            return
        with lock:
            try:
                print("REQ %s %s" % (self.command, path), flush=True)
                self._send_json(routes[path](body))
            except ValueError as exc:
                self._send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("Elite Division provisional running on http://0.0.0.0:%d" % PORT, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()