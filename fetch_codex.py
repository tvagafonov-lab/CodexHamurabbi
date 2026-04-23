"""
CodexHamurabbi — fetch usage from Codex Desktop.

Prefers `~/.codex/logs_2.sqlite` (the current storage — Codex Desktop emits
`codex.rate_limits` websocket events there on every turn), and falls back
to `~/.codex/sessions/*.jsonl` for older Codex versions that still wrote
`event_msg/token_count` records. No network calls, no auth.
"""
import json, os, re, sqlite3, time
from pathlib import Path
from datetime import datetime, timezone

CODEX_HOME   = Path(os.environ.get("USERPROFILE", Path.home())) / ".codex"
CACHE_FILE   = CODEX_HOME / "hamurabbi_cache.json"
CODEX_SQLITE = CODEX_HOME / "logs_2.sqlite"

_TAIL_BYTES = 64 * 1024   # enough for ~hundreds of recent events
_WS_EVENT_RE = re.compile(r'websocket event:\s*(\{.*\})\s*$')
EXHAUSTION_PCT_FLOOR = 80.0


def _tail_lines(fpath: str, tail_bytes: int = _TAIL_BYTES) -> list[bytes]:
    """Return the last `tail_bytes` worth of complete lines from a file.
    Skips a leading partial line when we didn't start at offset 0."""
    try:
        size = os.path.getsize(fpath)
        start = max(0, size - tail_bytes)
        with open(fpath, "rb") as fh:
            if start:
                fh.seek(start)
            data = fh.read()
    except OSError:
        return []
    lines = data.split(b"\n")
    if start and lines:
        lines = lines[1:]   # first chunk is likely a mid-line fragment
    return lines


def _aggregate_events(events: list) -> dict | None:
    """Aggregate rate_limits events into a single freshest snapshot.

    Multiple Codex sessions run concurrently; within a single window (same
    `resets_at`) usage is monotonically non-decreasing, so MAX(used_percent)
    across events sharing that `resets_at` is the freshest observation.

    Exhaustion: when Codex hits the cap it flips `limit_id` to "premium"
    and nulls out `primary`/`secondary`. We synthesise 100 % against the
    last-known `resets_at` for that sub-limit — but only if its last
    known pct was already ≥ 80 % (prevents a `secondary=null` event from
    bumping an unrelated 15 % weekly to 100 %).
    """
    if not events:
        return None
    events.sort(key=lambda e: e[0])   # timestamp-asc

    buckets: dict[str, dict] = {"primary": {}, "secondary": {}, "credits": {}}
    meta: dict = {}
    last_state: dict[str, tuple] = {}   # key -> (resets_at, max_pct_this_window)

    for _ts, rl in events:
        meta = {"limit_id":  rl.get("limit_id"),
                "plan_type": rl.get("plan_type")}
        exhausted = rl.get("limit_id") == "premium"

        for key in ("primary", "secondary", "credits"):
            sub = rl.get(key)
            if sub:
                ra = sub.get("resets_at")
                if ra is None:
                    continue
                pct = sub.get("used_percent") or 0
                bucket = buckets[key]
                best = bucket.get(ra)
                if best is None or pct > (best.get("used_percent") or 0):
                    bucket[ra] = sub
                if key in ("primary", "secondary"):
                    prev_ra, prev_max = last_state.get(key, (None, 0.0))
                    last_state[key] = (ra, max(prev_max, pct) if prev_ra == ra else pct)
            elif (exhausted and key in ("primary", "secondary")
                  and key in last_state
                  and last_state[key][1] >= EXHAUSTION_PCT_FLOOR):
                ra = last_state[key][0]
                bucket = buckets[key]
                prev = bucket.get(ra, {})
                bucket[ra] = {**prev, "used_percent": 100.0, "resets_at": ra}

    result: dict = {**meta}
    has_any = False
    for key, bucket in buckets.items():
        if not bucket:
            result[key] = None
            continue
        latest_ra = max(bucket.keys())
        result[key] = bucket[latest_ra]
        has_any = True
    return result if has_any else None


def _events_from_sqlite() -> list:
    """Read `codex.rate_limits` websocket events from logs_2.sqlite.
    Codex Desktop now writes rate_limits here on every turn; the old JSONL
    token_count records have become sporadic."""
    if not CODEX_SQLITE.exists():
        return []
    try:
        # Read-only URI avoids blocking the writer process.
        conn = sqlite3.connect(
            f"file:{CODEX_SQLITE.as_posix()}?mode=ro", uri=True, timeout=3.0)
    except sqlite3.Error:
        return []
    try:
        cutoff = int(time.time()) - 24 * 3600
        rows = conn.execute(
            "SELECT ts, feedback_log_body FROM logs "
            "WHERE ts >= ? AND feedback_log_body LIKE '%codex.rate_limits%' "
            "ORDER BY ts ASC",
            (cutoff,)
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()

    events: list[tuple[str, dict]] = []
    for ts, body in rows:
        if not body:
            continue
        m = _WS_EVENT_RE.search(body)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        rl = data.get("rate_limits") or {}
        if not rl:
            continue
        # Normalize SQLite event shape to the JSONL-compatible one used by
        # _aggregate_events: rename `reset_at` → `resets_at`, synthesise
        # `limit_id` from `limit_reached`.
        normalized: dict = {
            "plan_type": data.get("plan_type"),
            "limit_id":  "premium" if rl.get("limit_reached") else "codex",
        }
        for key in ("primary", "secondary"):
            sub = rl.get(key)
            if sub:
                normalized[key] = {
                    "used_percent":   sub.get("used_percent"),
                    "window_minutes": sub.get("window_minutes"),
                    "resets_at":      sub.get("reset_at"),
                }
            else:
                normalized[key] = None
        normalized["credits"] = data.get("credits")
        events.append((str(ts), normalized))
    return events


def _events_from_jsonl() -> list:
    """Tail the last ~30 active JSONL sessions for `token_count` events.
    Fallback for older Codex versions that still wrote rate_limits there."""
    sessions_dir = CODEX_HOME / "sessions"
    if not sessions_dir.exists():
        return []

    cutoff = time.time() - 24 * 3600
    files: list[tuple[float, str]] = []
    for root, _dirs, names in os.walk(str(sessions_dir)):
        for fname in names:
            if fname.endswith(".jsonl"):
                fpath = os.path.join(root, fname)
                try:
                    mt = os.path.getmtime(fpath)
                    if mt >= cutoff:
                        files.append((mt, fpath))
                except OSError:
                    pass
    files.sort(reverse=True)

    events: list[tuple[str, dict]] = []
    for _mt, fpath in files[:30]:
        for raw in _tail_lines(fpath):
            if not raw or b'"token_count"' not in raw:
                continue
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            if ev.get("type") != "event_msg":
                continue
            p = ev.get("payload") or {}
            if p.get("type") != "token_count":
                continue
            rl = p.get("rate_limits") or {}
            if not rl:
                continue
            events.append((ev.get("timestamp", ""), rl))
    return events


def _latest_rate_limits() -> dict | None:
    """Freshest rate_limits snapshot, merged from both sources.

    Codex writes rate_limits to both `logs_2.sqlite` (websocket events, every
    turn) and the legacy JSONL session files (token_count events, sporadic
    across builds). Freshness drifts between the two depending on Codex
    version, so we concatenate both event streams and let `_aggregate_events`
    pick MAX-per-resets_at across everything — whichever source saw the
    highest usage within the current window wins."""
    return _aggregate_events(_events_from_sqlite() + _events_from_jsonl())


def fetch_and_save() -> dict:
    rl = _latest_rate_limits()

    if rl is None:
        result = {"error": "no_data"}
        CACHE_FILE.write_text(json.dumps(result), encoding="utf-8")
        return result

    pri = rl.get("primary")   or {}   # 5-hour window
    sec = rl.get("secondary") or {}   # weekly
    cr  = rl.get("credits")   or {}   # extra credits

    result = {
        "fh_pct":   pri.get("used_percent", 0),
        "fh_reset": pri.get("resets_at"),       # Unix timestamp
        "wd_pct":   sec.get("used_percent", 0),
        "wd_reset": sec.get("resets_at"),        # Unix timestamp
        "cr_pct":   cr.get("used_percent", 0)  if cr else 0,
        "cr_used":  cr.get("used_credits", 0)  if cr else 0,
        "cr_limit": cr.get("monthly_limit", 0) if cr else 0,
        "cr_curr":  cr.get("currency", "")     if cr else "",
        "plan":     rl.get("plan_type", ""),
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    CACHE_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    import pprint
    pprint.pprint(fetch_and_save())
