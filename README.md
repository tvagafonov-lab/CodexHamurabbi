# CodexHamurabbi

> **The only usage monitor built specifically for the Codex Desktop app (Windows).**  
> No API calls, no auth tokens — reads directly from Codex's local session files.

Compact always-on-top overlay that shows your real-time **Codex usage stats** — 5-hour window, weekly limit, and extra credits — directly on your desktop.

<img src="docs/screenshot-full.png" alt="Full mode" width="265"> <img src="docs/screenshot-compact.png" alt="Compact mode" width="165">

---

## What it shows

| Metric | Description |
|---|---|
| ⏱ **5h window** | 5-hour rolling window usage & time to reset |
| 📅 **Week** | 7-day total usage & time to reset |
| 💳 **Credits** | Extra credits used / monthly limit |

Color-coded progress bars / rings: green → yellow → red as limits approach.

**Three modes:**
- **Full** — progress bars + labels + reset countdowns
- **Compact** — icon + % + time to reset (165 px wide)
- **Dock** — donut-ring strip (44 px tall) that snaps above the Windows taskbar

Double-click the header to toggle full ↔ compact. Right-click → ⊞ Dock mode to go minimal.

---

## Languages

Right-click the overlay → **🌐 Language** to switch instantly. Setting persists across restarts.

| Code | Language |
|---|---|
| `en` | English |
| `fr` | Français |
| `es` | Español |
| `ru` | Русский |
| `lg` | Luganda |

Want to add your language? Edit [`i18n.py`](i18n.py) — copy any block, add a new key, translate the values. One file, no build step.

---

## Requirements

- **Windows 10 / 11**
- **Python 3.10+** — [python.org/downloads](https://python.org/downloads/) *(check "Add Python to PATH" during install)*
- **Codex Desktop app** — any paid plan

No external Python packages required — uses stdlib `json`, `glob`, and `tkinter` only.

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
%USERPROFILE%\.codex\sessions\YYYY\MM\DD\*.jsonl
```
which Codex Desktop writes automatically during every session.

---

## Usage

**Controls:**
| Action | Result |
|---|---|
| Drag | Move the window anywhere |
| Double-click header | Toggle compact / full mode |
| Double-click (dock) | Exit dock mode |
| Right-click | Context menu (mode, %, opacity, language, close) |
| ✕ button | Close |

Settings saved to `%USERPROFILE%\.codex\hamurabbi_settings.json`.

---

## How it works

Codex Desktop writes session events to local JSONL files:
```
%USERPROFILE%\.codex\sessions\YYYY\MM\DD\<session-id>.jsonl
```

Each file contains `event_msg` events. CodexHamurabbi scans these files, finds the most recent `token_count` event, and reads the `rate_limits` object — the same limits displayed in the Codex app settings. No network requests. No authentication.

```
rate_limits:
  primary    → 5-hour rolling window (used_percent, resets_at)
  secondary  → weekly total (used_percent, resets_at)
  credits    → extra credits (used_credits, monthly_limit)
```

The overlay watches session files for changes (checks every 5 s) and re-reads immediately when Codex writes new token counts — no manual refresh needed.

---

## Files

```
CodexHamurabbi/
├── codex_monitor.py   # Main overlay (tkinter)
├── fetch_codex.py     # Reads rate_limits from JSONL session files
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

**Shows 0% on all bars**  
→ Make sure Codex Desktop has run at least one session. The overlay scans all sessions from the past 8 days.

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
