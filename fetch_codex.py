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
from urllib.error import URLError

CODEX_HOME = Path(os.environ.get("USERPROFILE", Path.home())) / ".codex"
CACHE_FILE = CODEX_HOME / "hamurabbi_cache.json"
AUTH_FILE  = CODEX_HOME / "auth.json"
LOG_FILE   = CODEX_HOME / "hamurabbi_fetch.log"

USAGE_URL = "https://chatgpt.com/backend-api/codex/usage"


def _log(msg: str) -> None:
    """Append a one-line entry to the fetch log; auto-rotates at 64 KB."""
    try:
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 64_000:
            tail = LOG_FILE.read_text(encoding="utf-8", errors="ignore")[-32_000:]
            LOG_FILE.write_text(tail, encoding="utf-8")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{ts}  {msg}\n")
    except OSError:
        pass


def _fetch_usage() -> tuple[dict | None, str]:
    """Returns (data, reason). On success, reason is "". On failure, reason
    explains *why* — caller decides whether the failure should overwrite the
    last-known-good cache."""
    try:
        auth = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        tokens     = auth.get("tokens") or {}
        token      = tokens.get("access_token")
        account_id = tokens.get("account_id", "")
    except (OSError, ValueError) as e:
        return None, f"auth_read_fail:{type(e).__name__}"
    if not token:
        return None, "auth_no_token"

    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization":       f"Bearer {token}",
        "Accept":              "application/json",
        "User-Agent":          "CodexHamurabbi/1.0",
        "chatgpt-account-id":  account_id,
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), ""
    except (URLError, TimeoutError, ValueError, OSError) as e:
        return None, f"http_fail:{type(e).__name__}"


def _write_if_changed(result: dict) -> None:
    """Skip the write (and mtime bump) when only `fetched_at` differs.
    The overlay re-reads on mtime change; unconditional writes force a
    repaint every poll even when nothing moved."""
    payload = json.dumps(result, indent=2)
    try:
        prev = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = None
    if isinstance(prev, dict):
        a = {k: v for k, v in result.items() if k != "fetched_at"}
        b = {k: v for k, v in prev.items()   if k != "fetched_at"}
        if a == b:
            return
    CACHE_FILE.write_text(payload, encoding="utf-8")


def _cache_has_valid_data() -> bool:
    """True if the on-disk cache currently holds a successful fetch result
    (not a sentinel `{"error": "no_data"}`)."""
    try:
        prev = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(prev, dict) and "error" not in prev and "fh_pct" in prev


def fetch_and_save() -> dict:
    data, reason = _fetch_usage()
    if data is None:
        _log(f"fail:  {reason}")
        # CRITICAL: a transient fetch failure (Wi-Fi blip, sleep wake,
        # 503 from upstream, ~) must NOT overwrite the last successful
        # snapshot — the overlay would lose its real percentages and
        # render all rows at 0%. We only persist {"error": "no_data"}
        # when the cache was already empty / errored (i.e., first run
        # or previous failure), so the overlay header still shows the
        # warning instead of leaving a stale ⟳ timestamp.
        if not _cache_has_valid_data():
            _write_if_changed({"error": "no_data"})
        return {"error": "no_data"}

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
        "cr_used":  float(cr.get("balance") or 0),
        "cr_limit": 0,
        "cr_curr":  "",
        "plan":     data.get("plan_type", ""),
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    _write_if_changed(result)
    _log(f"ok:    fh={result['fh_pct']} wd={result['wd_pct']} plan={result['plan']}")
    return result


if __name__ == "__main__":
    import pprint
    pprint.pprint(fetch_and_save())
