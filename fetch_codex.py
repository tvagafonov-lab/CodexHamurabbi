"""
CodexHamurabbi — fetch usage from Codex Desktop JSONL session files.
Reads the most recent token_count event with rate_limits — no API call needed.
Scans by file modification time so long-running sessions (started days ago)
are always picked up correctly.

Only reads the tail of each file (64 KB by default). token_count events are
appended near the end of active sessions, so this avoids re-scanning the
entire history on every fetch — critical for 20+ MB session files.
"""
import json, os, time
from pathlib import Path
from datetime import datetime, timezone

CODEX_HOME = Path(os.environ.get("USERPROFILE", Path.home())) / ".codex"
CACHE_FILE = CODEX_HOME / "hamurabbi_cache.json"

_TAIL_BYTES = 64 * 1024   # enough for ~hundreds of recent events


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


def _latest_rate_limits() -> dict | None:
    """Walk JSONL session files and return the freshest rate_limits snapshot.

    Multiple Codex sessions run concurrently and each session caches its own
    copy of rate_limits (refreshed only when that session makes an API call).
    Picking by event timestamp alone can surface a stale 3 % from a quiet
    session and miss the 65 % one that a neighbour just observed.

    Strategy: within a single rate-limit window (same `resets_at`), usage is
    monotonically non-decreasing, so MAX(used_percent) among events sharing
    that `resets_at` is the freshest observation.

    Special case — limit exhaustion: when Codex hits the cap it flips
    `limit_id` from "codex" to "premium" and rewrites `primary` (and/or
    `secondary`) to `null`. Naively skipping those events leaves the display
    frozen at the last pre-cap reading (e.g. 93 %). We detect this marker
    and synthesise a 100 % entry against the most recently seen `resets_at`
    for that sub-limit.
    """
    sessions_dir = CODEX_HOME / "sessions"
    if not sessions_dir.exists():
        return None

    # Cutoff keeps the file set bounded while still tolerating overnight /
    # weekend gaps. Tail-read is ~20 ms per file so even 30 files stays fast.
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

    # Pass 1: gather every token_count event's rate_limits dict.
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

    # Process in event-timestamp order so an exhaustion flag is always
    # applied to the same window it was observed in.
    events.sort(key=lambda e: e[0])

    buckets: dict[str, dict] = {"primary": {}, "secondary": {}, "credits": {}}
    meta: dict = {}
    # Per sub-limit, track the current window's (resets_at, max_pct_seen).
    # When resets_at changes we start fresh — old window's 88 % must not
    # leak into the new window's exhaustion check.
    last_state: dict[str, tuple] = {}   # key -> (ra, max_pct)
    # A sub-limit is counted as the exhaustion cause only if its last known
    # value was already near the cap — otherwise `primary=null` alone would
    # also promote an unrelated 15 % weekly to 100 %.
    EXHAUSTION_PCT_FLOOR = 80.0

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
                # Codex blanked this sub-limit after capping out — the
                # real value is 100 % of the last window we saw.
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
