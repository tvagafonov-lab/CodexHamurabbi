"""
CodexHamurabbi — fetch Codex Desktop usage via chatgpt.com/backend-api.

Codex Desktop stopped emitting `codex.rate_limits` events to local sinks
(logs_2.sqlite websocket events & JSONL `token_count` records) in
v0.122.0-alpha.13+, so the old file-parsing approach drifts hours behind
real usage. The Desktop UI itself reads rate limits from the backend
endpoint below — we do the same, using the Chatgpt access_token Desktop
already caches in `~/.codex/auth.json`. No password, no cookie juggling,
and Desktop refreshes the token in-place so re-reading the file each
fetch keeps us current.
"""
import json, os, urllib.request
from pathlib import Path
from datetime import datetime, timezone

CODEX_HOME = Path(os.environ.get("USERPROFILE", Path.home())) / ".codex"
CACHE_FILE = CODEX_HOME / "hamurabbi_cache.json"
AUTH_FILE  = CODEX_HOME / "auth.json"

USAGE_URL = "https://chatgpt.com/backend-api/codex/usage"


def _fetch_usage() -> dict | None:
    try:
        auth = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        tokens     = auth.get("tokens") or {}
        token      = tokens.get("access_token")
        account_id = tokens.get("account_id", "")
    except (OSError, ValueError):
        return None
    if not token:
        return None

    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization":       f"Bearer {token}",
        "Accept":              "application/json",
        "User-Agent":          "CodexHamurabbi/1.0",
        "chatgpt-account-id":  account_id,
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


def fetch_and_save() -> dict:
    data = _fetch_usage()
    if data is None:
        result = {"error": "no_data"}
        CACHE_FILE.write_text(json.dumps(result), encoding="utf-8")
        return result

    rl  = data.get("rate_limit") or {}
    pri = rl.get("primary_window")   or {}   # 5-hour window
    sec = rl.get("secondary_window") or {}   # weekly
    cr  = data.get("credits")        or {}

    result = {
        "fh_pct":   pri.get("used_percent", 0),
        "fh_reset": pri.get("reset_at"),
        "wd_pct":   sec.get("used_percent", 0),
        "wd_reset": sec.get("reset_at"),
        "cr_pct":   0,
        "cr_used":  0 if cr.get("balance") in (None, "") else float(cr.get("balance") or 0),
        "cr_limit": 0,
        "cr_curr":  "",
        "plan":     data.get("plan_type", ""),
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    CACHE_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    import pprint
    pprint.pprint(fetch_and_save())
