"""
CodexHamurabbi — fetch usage from Codex Desktop JSONL session files.
Reads the most recent token_count event with rate_limits — no API call needed.
"""
import json, glob, os
from pathlib import Path
from datetime import datetime, timezone, timedelta

CODEX_HOME = Path(os.environ.get("USERPROFILE", Path.home())) / ".codex"
CACHE_FILE = CODEX_HOME / "hamurabbi_cache.json"


def _latest_rate_limits() -> dict | None:
    """Scan recent JSONL sessions newest-first, return the last rate_limits seen."""
    now = datetime.now(tz=timezone.utc)
    best = None  # (timestamp, rate_limits)

    for days_back in range(4):
        d = now - timedelta(days=days_back)
        pattern = str(CODEX_HOME / "sessions" /
                      f"{d.year}" / f"{d.month:02d}" / f"{d.day:02d}" / "*.jsonl")
        for fpath in sorted(glob.glob(pattern), reverse=True):
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

        if best and days_back == 0:
            break   # found something today — stop early

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
