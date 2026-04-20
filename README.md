# CodexHamurabbi

> **The only usage monitor built specifically for the Codex Desktop app (Windows).**  
> No API calls, no auth tokens — reads directly from Codex's local SQLite database.

Compact always-on-top overlay that shows your real-time **Codex usage stats** — tokens today, tokens this week, active sessions — directly on your desktop.

<img src="docs/screenshot-full.png" alt="Full mode" width="265"> <img src="docs/screenshot-compact.png" alt="Compact mode" width="165">

---

## What it shows

| Metric | Description |
|---|---|
| 📅 **Today** | Tokens used today across all sessions |
| 📆 **7 days** | Tokens used over the last 7 days |
| 🔀 **Sessions** | Number of Codex sessions today |

Color-coded progress bars scale visually (100M = full bar today, 500M = full bar weekly).

**Two modes:** full (with progress bars) and compact (icon + value). Double-click the header to switch.

---

## Languages

Right-click → **🌐 Language** to switch instantly. Persists across restarts.

| Code | Language |
|---|---|
| `en` | English |
| `fr` | Français |
| `es` | Español |
| `ru` | Русский |
| `lg` | Luganda |

Want to add yours? Edit [`i18n.py`](i18n.py) — one block, no build step.

---

## Requirements

- **Windows 10 / 11**
- **Python 3.10+** — [python.org/downloads](https://python.org/downloads/) *(check "Add Python to PATH")*
- **Codex Desktop app** — [openai.com/codex](https://openai.com/codex) (any paid plan)

No external Python packages required — uses stdlib `sqlite3` and `tkinter` only.

---

## Installation

### 1. Download

Click **Code → Download ZIP**, extract anywhere.

Or clone:
```
git clone https://github.com/tvagafonov-lab/CodexHamurabbi.git
cd CodexHamurabbi
```

### 2. Run

Double-click **`start_monitor.bat`**

That's it. No setup wizard, no API keys. The overlay reads from:
```
%USERPROFILE%\.codex\state_5.sqlite
```
which Codex Desktop writes automatically.

---

## Usage

**Controls:**
| Action | Result |
|---|---|
| Drag | Move the window anywhere |
| Double-click header | Toggle compact / full mode |
| Right-click | Context menu (interval, opacity, language, refresh) |
| ↺ button | Force refresh |
| ✕ button | Close |

Settings saved to `%USERPROFILE%\.codex\hamurabbi_settings.json`.

---

## How it works

Codex Desktop stores session data in a local SQLite database:
```
%USERPROFILE%\.codex\state_5.sqlite   →   table: threads (tokens_used, updated_at)
```

CodexHamurabbi opens this file in read-only mode, aggregates `tokens_used` by day, and displays the result. No network requests. No authentication.

Data refreshes every **5 minutes** by default (configurable: 1 / 5 / 10 / 30 min).

---

## Files

```
CodexHamurabbi/
├── codex_monitor.py   # Main overlay (tkinter)
├── fetch_codex.py     # Reads from state_5.sqlite
├── i18n.py            # Translations — edit to add a language
├── start_monitor.bat  # Launch overlay
└── requirements.txt   # (empty — no dependencies)
```

---

## Also using Claude Desktop?

Check out the sibling project:

**[JeanClaudeCombien](https://github.com/tvagafonov-lab/JeanClaudeCombien)** — same idea for Claude Desktop.  
Shows 5h window, weekly limit, Sonnet usage, Design, and extra credits.

---

## Troubleshooting

**Window not visible**  
→ Delete `%USERPROFILE%\.codex\hamurabbi_settings.json` and restart — reappears bottom-right.

**Shows 0 tokens**  
→ Make sure Codex Desktop has run at least one session today.

**"Python not found"**  
→ Reinstall Python from [python.org](https://python.org/downloads/) and check **"Add Python to PATH"**.

---

## Privacy

All data is local. No outbound requests. No telemetry.

---

## Support the project

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/agafonov)

---

## License

MIT
