"""
CodexHamurabbi — fetch usage from Codex Desktop SQLite.
No API calls, no auth — reads local state_5.sqlite directly.
"""
import json, sqlite3, os
from pathlib import Path
from datetime import datetime, timezone

CODEX_HOME = Path(os.environ.get("USERPROFILE", Path.home())) / ".codex"
STATE_DB   = CODEX_HOME / "state_5.sqlite"
CACHE_FILE = CODEX_HOME / "hamurabbi_cache.json"

# Visual scale caps for progress bars (not hard limits)
SCALE_TODAY = 100_000_000   # 100M tokens = full bar today
SCALE_WEEK  = 500_000_000   # 500M tokens = full bar week


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000_000: return f"{n/1e9:.1f}B"
    if n >= 1_000_000:     return f"{n/1e6:.1f}M"
    if n >= 1_000:         return f"{n/1000:.0f}K"
    return str(n)


def fetch_and_save() -> dict:
    if not STATE_DB.exists():
        return {"error": "no_db"}

    try:
        db = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)

        # Today
        row_today = db.execute("""
            SELECT COALESCE(SUM(tokens_used),0), COALESCE(COUNT(*),0)
            FROM threads
            WHERE date(updated_at, 'unixepoch') = date('now')
              AND tokens_used > 0
        """).fetchone()
        today_tokens, today_sessions = row_today

        # This week (7 days)
        row_week = db.execute("""
            SELECT COALESCE(SUM(tokens_used),0)
            FROM threads
            WHERE updated_at > strftime('%s','now','-7 days')
              AND tokens_used > 0
        """).fetchone()
        week_tokens = row_week[0]

        # Yesterday
        row_yesterday = db.execute("""
            SELECT COALESCE(SUM(tokens_used),0)
            FROM threads
            WHERE date(updated_at, 'unixepoch') = date('now','-1 day')
              AND tokens_used > 0
        """).fetchone()
        yesterday_tokens = row_yesterday[0]

        # Most-used model today
        row_model = db.execute("""
            SELECT model, SUM(tokens_used) as t
            FROM threads
            WHERE date(updated_at, 'unixepoch') = date('now')
              AND tokens_used > 0
            GROUP BY model ORDER BY t DESC LIMIT 1
        """).fetchone()
        last_model = row_model[0] if row_model else "—"

        db.close()

        result = {
            "today_tokens":    today_tokens,
            "today_sessions":  today_sessions,
            "yesterday_tokens": yesterday_tokens,
            "week_tokens":     week_tokens,
            "last_model":      last_model,
            "today_pct":       min(today_tokens / SCALE_TODAY * 100, 100),
            "week_pct":        min(week_tokens  / SCALE_WEEK  * 100, 100),
            "today_fmt":       _fmt_tokens(today_tokens),
            "yesterday_fmt":   _fmt_tokens(yesterday_tokens),
            "week_fmt":        _fmt_tokens(week_tokens),
            "fetched_at":      datetime.now(tz=timezone.utc).isoformat(),
        }

    except Exception as e:
        result = {"error": str(e)[:80]}

    CACHE_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    import pprint
    pprint.pprint(fetch_and_save())
