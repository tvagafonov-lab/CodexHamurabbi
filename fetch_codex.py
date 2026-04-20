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
    """Walk JSONL session files newest-mtime-first and return rate_limits from
    the token_count event with the latest timestamp. Only the tail of each
    file is read."""
    sessions_dir = CODEX_HOME / "sessions"
    if not sessions_dir.exists():
        return None

    cutoff = time.time() - 8 * 24 * 3600   # ignore files older than 8 days

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

    best: tuple[str, dict] | None = None   # (iso_timestamp, rate_limits)

    for mt, fpath in files[:30]:
        # If this file was last written before the best event we've already
        # found, no line inside can improve on it.
        if best is not None:
            try:
                if mt < datetime.fromisoformat(
                        best[0].replace("Z", "+00:00")).timestamp():
                    break
            except ValueError:
                pass
        for raw in _tail_lines(fpath):
            if not raw or b'"token_count"' not in raw:
                continue   # cheap early reject before json.loads
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            if ev.get("type") != "event_msg":
                continue
            p = ev.get("payload") or {}
            rl = p.get("rate_limits")
            if rl and p.get("type") == "token_count":
                ts = ev.get("timestamp", "")
                if best is None or ts > best[0]:
                    best = (ts, rl)

    return best[1] if best else None


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
