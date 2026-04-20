"""
CodexHamurabbi — fetch usage from Codex Desktop JSONL session files.
Reads the most recent token_count event with rate_limits — no API call needed.
Scans by file modification time so long-running sessions (started days ago)
are always picked up correctly.
"""
import json, os, time
from pathlib import Path
from datetime import datetime, timezone

CODEX_HOME = Path(os.environ.get("USERPROFILE", Path.home())) / ".codex"
CACHE_FILE = CODEX_HOME / "hamurabbi_cache.json"


def _latest_rate_limits() -> dict | None:
    """
    Walk all JSONL session files sorted by mtime (newest first).
    Return the rate_limits from the token_count event with the latest timestamp.
    """
    sessions_dir = CODEX_HOME / "sessions"
    if not sessions_dir.exists():
        return None

    cutoff = time.time() - 8 * 24 * 3600   # ignore files older than 8 days

    # Collect all JSONL files with their mtime
    all_files: list[tuple[float, str]] = []
    for root, _dirs, files in os.walk(str(sessions_dir)):
        for fname in files:
            if fname.endswith(".jsonl"):
                fpath = os.path.join(root, fname)
                try:
                    mt = os.path.getmtime(fpath)
                    if mt >= cutoff:
                        all_files.append((mt, fpath))
                except OSError:
                    pass

    # Newest-modified first — read up to 30 files
    all_files.sort(reverse=True)

    best: tuple[str, dict] | None = None   # (iso_timestamp, rate_limits)

    for _mt, fpath in all_files[:30]:
        try:
            with open(fpath, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    try:
                        ev = json.loads(line)
                        if ev.get("type") != "event_msg":
                            continue
                        p = ev.get("payload", {})
                        rl = p.get("rate_limits")
                        if rl and p.get("type") == "token_count":
                            ts = ev.get("timestamp", "")
                            if best is None or ts > best[0]:
                                best = (ts, rl)
                    except Exception:
                        pass
        except Exception:
            pass

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
