#!/usr/bin/env python3
"""
CodexHamurabbi — Codex Desktop usage overlay for Windows.
Reads rate limits from ~/.codex/sessions JSONL — no auth, no API calls.
Double-click header to toggle compact mode. Right-click for settings.
"""

import tkinter as tk
import json, os, time, subprocess, sys, threading
from pathlib import Path
from datetime import datetime, timezone
import i18n

# ── Paths ─────────────────────────────────────────────────────────────────────
CODEX_HOME    = Path(os.environ.get("USERPROFILE", Path.home())) / ".codex"
CACHE_FILE    = CODEX_HOME / "hamurabbi_cache.json"
SETTINGS_FILE = CODEX_HOME / "hamurabbi_settings.json"
FETCH_SCRIPT  = Path(__file__).parent / "fetch_codex.py"

DEFAULT_SETTINGS = {
    "opacity":  0.92,
    "interval": 300,
    "compact":  False,
    "lang":     "en",
    "pos_x":    -1,
    "pos_y":    -1,
}

# ── Colors — Hammurabi gold on dark stone ─────────────────────────────────────
C = {
    "bg":     "#0d0a04",
    "bg2":    "#1c1608",
    "hdr":    "#261e0c",
    "accent": "#d4a017",   # Hammurabi gold
    "text":   "#f5e8c0",   # warm cream
    "muted":  "#6b5a2a",
    "green":  "#7ecf6e",
    "yellow": "#e8a020",
    "red":    "#e06050",
    "bar":    "#2a2010",
}

W_FULL    = 265
W_COMPACT = 165

# Row definitions: (pct_key, reset_key, icon, i18n_key)
ROWS = [
    ("fh_pct", "fh_reset", "⏱", "row_5h"),
    ("wd_pct", "wd_reset", "📅", "row_week"),
    ("cr_pct", None,       "💳", "row_credits"),
]


# ── Settings ──────────────────────────────────────────────────────────────────
class Settings:
    def __init__(self):
        self._d = DEFAULT_SETTINGS.copy()
        try:
            if SETTINGS_FILE.exists():
                self._d.update(json.loads(SETTINGS_FILE.read_text("utf-8")))
        except Exception:
            pass

    def save(self):
        try:
            SETTINGS_FILE.write_text(json.dumps(self._d, indent=2), "utf-8")
        except Exception:
            pass

    def __getitem__(self, k):    return self._d.get(k)
    def __setitem__(self, k, v): self._d[k] = v; self.save()


# ── Helpers ───────────────────────────────────────────────────────────────────
def read_cache() -> dict:
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text("utf-8"))
    except Exception:
        pass
    return {}


def fmt_reset(unix_ts: int | None, lang: str) -> str:
    """Format a Unix int timestamp into a human-readable countdown."""
    tr = i18n.STRINGS.get(lang, i18n.STRINGS["en"])
    if not unix_ts:
        return "—"
    try:
        dt   = datetime.fromtimestamp(int(unix_ts), tz=timezone.utc)
        diff = dt - datetime.now(tz=timezone.utc)
        if diff.total_seconds() < 0:
            return tr["reset_done"]
        mins = int(diff.total_seconds() // 60)
        h, m = divmod(mins, 60)
        if diff.total_seconds() < 86400:
            return f"{h}h {m:02}m" if h else f"{m}m"
        local = dt.astimezone()
        return f"{tr['days'][local.weekday()]} {local.strftime('%H:%M')}"
    except Exception:
        return "—"


def bar_color(pct: float) -> str:
    if pct >= 90: return C["red"]
    if pct >= 60: return C["yellow"]
    return C["green"]


def pct_color(pct: float) -> str:
    if pct >= 90: return C["red"]
    if pct >= 60: return C["yellow"]
    return C["text"]


# ── Main window ───────────────────────────────────────────────────────────────
class CodexHamurabbi:
    def __init__(self):
        self.cfg         = Settings()
        self.root        = tk.Tk()
        self._body       = None
        self._rows_widgets = []
        self._known_mtime = 0.0   # latest mtime seen across all session files
        self._build_window()
        self._build_content()
        self._fit_height()
        self._refresh_ui()
        self._schedule_bg_fetch()

    def _t(self, key: str, **kwargs) -> str:
        return i18n.get(self.cfg["lang"], key, **kwargs)

    # ── Window (built once) ───────────────────────────────────────────────────
    def _build_window(self):
        r = self.root
        r.title("CodexHamurabbi")
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.attributes("-alpha", self.cfg["opacity"])
        r.configure(bg=C["bg"])

        W = W_COMPACT if self.cfg["compact"] else W_FULL
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        x = self.cfg["pos_x"] if self.cfg["pos_x"] >= 0 else sw - W - 20
        y = self.cfg["pos_y"] if self.cfg["pos_y"] >= 0 else sh - 200 - 60
        r.geometry(f"{W}x200+{x}+{y}")

        r.bind("<Button-1>",        self._drag_start)
        r.bind("<B1-Motion>",       self._drag_move)
        r.bind("<ButtonRelease-1>", self._drag_end)
        r.bind("<Button-3>",        self._ctx_menu)

        # Header (permanent — survives mode/language rebuilds)
        hdr = tk.Frame(r, bg=C["hdr"], height=24)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        self._title_var = tk.StringVar(value="◆ CodexHamurabbi")
        hdr_lbl = tk.Label(hdr, textvariable=self._title_var,
                           bg=C["hdr"], fg=C["accent"],
                           font=("Segoe UI", 8, "bold"), cursor="hand2")
        hdr_lbl.pack(side="left", padx=7)
        hdr_lbl.bind("<Double-Button-1>", lambda _: self._toggle_compact())

        x_lbl = tk.Label(hdr, text="✕", bg=C["hdr"], fg=C["muted"],
                         font=("Segoe UI", 10), cursor="hand2")
        x_lbl.pack(side="right", padx=5)
        x_lbl.bind("<Button-1>", lambda _: r.destroy())
        x_lbl.bind("<Enter>",    lambda _: x_lbl.config(fg=C["red"]))
        x_lbl.bind("<Leave>",    lambda _: x_lbl.config(fg=C["muted"]))

        self._upd_var = tk.StringVar(value="")
        tk.Label(hdr, textvariable=self._upd_var,
                 bg=C["hdr"], fg=C["muted"],
                 font=("Segoe UI", 7)).pack(side="right", padx=3)

    # ── Content (rebuilt on mode / language change) ───────────────────────────
    def _build_content(self):
        if self._body:
            self._body.destroy()

        compact = self.cfg["compact"]
        lang    = self.cfg["lang"]
        W       = W_COMPACT if compact else W_FULL

        self._title_var.set("◆ CHB" if compact else "◆ CodexHamurabbi")

        self._body = tk.Frame(self.root, bg=C["bg"],
                              padx=6 if compact else 10)
        self._body.pack(fill="x", pady=(4, 5))

        self._rows_widgets = []
        for key_pct, key_rst, icon, name_key in ROWS:
            name = i18n.get(lang, name_key)
            if compact:
                w = self._make_compact_row(self._body, icon)
            else:
                w = self._make_full_row(self._body, icon, name)
            self._rows_widgets.append(w)

        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.root.geometry(f"{W}x1+{x}+{y}")

    def _make_full_row(self, parent, icon: str, name: str) -> dict:
        f = tk.Frame(parent, bg=C["bg"])
        f.pack(fill="x", pady=1)

        tk.Label(f, text=f"{icon} {name}", bg=C["bg"], fg=C["muted"],
                 font=("Segoe UI", 7), width=12, anchor="w").pack(side="left")

        canvas = tk.Canvas(f, height=5, bg=C["bar"],
                           highlightthickness=0, bd=0, width=62)
        canvas.pack(side="left", padx=(2, 3))

        pct_var = tk.StringVar(value="—")
        pct_lbl = tk.Label(f, textvariable=pct_var, bg=C["bg"], fg=C["text"],
                           font=("Segoe UI", 7), width=4, anchor="e")
        pct_lbl.pack(side="left")

        rst_var = tk.StringVar(value="")
        tk.Label(f, textvariable=rst_var, bg=C["bg"], fg=C["muted"],
                 font=("Segoe UI", 7)).pack(side="left", padx=(3, 0))

        return {"mode": "full", "canvas": canvas,
                "pct_var": pct_var, "pct_lbl": pct_lbl, "rst_var": rst_var}

    def _make_compact_row(self, parent, icon: str) -> dict:
        f = tk.Frame(parent, bg=C["bg"])
        f.pack(fill="x", pady=1)

        tk.Label(f, text=icon, bg=C["bg"], fg=C["muted"],
                 font=("Segoe UI", 8), width=2).pack(side="left")

        pct_var = tk.StringVar(value="—")
        pct_lbl = tk.Label(f, textvariable=pct_var, bg=C["bg"], fg=C["text"],
                           font=("Segoe UI", 8, "bold"), width=5, anchor="e")
        pct_lbl.pack(side="left")

        rst_var = tk.StringVar(value="")
        tk.Label(f, textvariable=rst_var, bg=C["bg"], fg=C["muted"],
                 font=("Segoe UI", 7)).pack(side="left", padx=(5, 0))

        return {"mode": "compact", "pct_var": pct_var, "pct_lbl": pct_lbl,
                "rst_var": rst_var}

    def _draw_bar(self, canvas: tk.Canvas, pct: float, color: str):
        canvas.update_idletasks()
        w = canvas.winfo_width() or 62
        canvas.delete("all")
        canvas.create_rectangle(0, 0, w, 5, fill=C["bar"], outline="")
        fw = int(w * min(pct, 100) / 100)
        if fw > 0:
            canvas.create_rectangle(0, 0, fw, 5, fill=color, outline="")

    def _fit_height(self):
        self.root.update_idletasks()
        h = self.root.winfo_reqheight()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        W = W_COMPACT if self.cfg["compact"] else W_FULL
        self.root.geometry(f"{W}x{h}+{x}+{y}")

    # ── Data refresh ──────────────────────────────────────────────────────────
    def _refresh_ui(self):
        cache = read_cache()
        lang  = self.cfg["lang"]

        for i, (key_pct, key_rst, icon, name_key) in enumerate(ROWS):
            pct   = float(cache.get(key_pct, 0))
            color = bar_color(pct)
            w     = self._rows_widgets[i]

            if key_rst is None:  # Credits row
                used    = cache.get("cr_used",  0)
                limit   = cache.get("cr_limit", 0)
                curr    = "€" if cache.get("cr_curr") == "EUR" else cache.get("cr_curr", "")
                rst_txt = f"{used:.2f} / {limit:.2f} {curr}"
            else:
                rst_txt = fmt_reset(cache.get(key_rst), lang)

            # Show remaining % (like Codex settings), bar still fills with used %
            remaining = max(0.0, 100.0 - pct)
            w["pct_var"].set(f"{remaining:.0f}%")
            w["pct_lbl"].config(fg=pct_color(pct))  # color by used (red = danger)
            w["rst_var"].set(rst_txt)

            if w["mode"] == "full":
                self.root.after(30 * i, lambda c=w["canvas"], p=pct, col=color:
                                self._draw_bar(c, p, col))

        if cache.get("fetched_at"):
            try:
                dt = datetime.fromisoformat(cache["fetched_at"])
                self._upd_var.set(f"⟳ {dt.astimezone().strftime('%H:%M')}")
            except Exception:
                pass

        self.root.after(10_000, self._refresh_ui)

    # ── Mode & language ───────────────────────────────────────────────────────
    def _toggle_compact(self):
        self.cfg["compact"] = not self.cfg["compact"]
        self._build_content()
        self.root.after(50, self._fit_height)
        self._refresh_ui()

    def _set_lang(self, lang: str):
        self.cfg["lang"] = lang
        self._build_content()
        self.root.after(50, self._fit_height)
        self._refresh_ui()

    # ── Background fetch ──────────────────────────────────────────────────────
    def _bg_fetch(self):
        def run():
            try:
                subprocess.run([sys.executable, str(FETCH_SCRIPT)],
                               timeout=20, capture_output=True)
                self.root.after(300, self._refresh_ui)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()
        self._upd_var.set("↻ …")

    def _find_latest_session_mtime(self) -> float:
        """Cheaply scan session dirs for the newest JSONL mtime."""
        sessions_dir = CODEX_HOME / "sessions"
        cutoff = time.time() - 8 * 24 * 3600
        latest = 0.0
        try:
            for root, _dirs, files in os.walk(str(sessions_dir)):
                for fname in files:
                    if fname.endswith(".jsonl"):
                        try:
                            mt = os.path.getmtime(os.path.join(root, fname))
                            if mt >= cutoff and mt > latest:
                                latest = mt
                        except OSError:
                            pass
        except Exception:
            pass
        return latest

    def _watch_sessions(self):
        """Every 5 s: re-fetch if any session file was modified since last fetch."""
        try:
            mt = self._find_latest_session_mtime()
            if mt > self._known_mtime:
                self._known_mtime = mt
                self._bg_fetch()
        except Exception:
            pass
        self.root.after(5_000, self._watch_sessions)

    def _schedule_bg_fetch(self):
        """Initial fetch on startup, then hand off to file watcher."""
        self._known_mtime = self._find_latest_session_mtime()
        self._bg_fetch()
        self.root.after(5_000, self._watch_sessions)

    # ── Drag ──────────────────────────────────────────────────────────────────
    def _drag_start(self, e): self._ox, self._oy = e.x, e.y
    def _drag_move(self, e):
        x = self.root.winfo_x() + e.x - self._ox
        y = self.root.winfo_y() + e.y - self._oy
        self.root.geometry(f"+{x}+{y}")
    def _drag_end(self, e):
        self.cfg["pos_x"] = self.root.winfo_x()
        self.cfg["pos_y"] = self.root.winfo_y()

    # ── Context menu ──────────────────────────────────────────────────────────
    def _ctx_menu(self, e):
        lang = self.cfg["lang"]
        m = tk.Menu(self.root, tearoff=0, bg=C["bg2"], fg=C["text"],
                    activebackground=C["accent"], font=("Segoe UI", 9), bd=0)

        mode_key = "menu_full" if self.cfg["compact"] else "menu_compact"
        m.add_command(label=self._t(mode_key), command=self._toggle_compact)
        # Opacity submenu
        sub2 = tk.Menu(m, tearoff=0, bg=C["bg2"], fg=C["text"],
                       activebackground=C["accent"], font=("Segoe UI", 9))
        for a in (1.0, 0.92, 0.80, 0.60):
            mark = "✓  " if abs(self.cfg["opacity"] - a) < 0.01 else "    "
            sub2.add_command(label=f"{mark}{int(a * 100)}%",
                             command=lambda a=a: self._set_opacity(a))
        m.add_cascade(label=self._t("menu_opacity"), menu=sub2)

        # Language submenu
        sub3 = tk.Menu(m, tearoff=0, bg=C["bg2"], fg=C["text"],
                       activebackground=C["accent"], font=("Segoe UI", 9))
        for code, label in i18n.LANGUAGES.items():
            mark = "✓  " if lang == code else "    "
            sub3.add_command(label=f"{mark}{label}",
                             command=lambda c=code: self._set_lang(c))
        m.add_cascade(label=self._t("menu_language"), menu=sub3)

        m.add_separator()
        m.add_command(label=self._t("menu_close"), command=self.root.destroy)
        m.post(e.x_root, e.y_root)

    def _set_opacity(self, v):
        self.cfg["opacity"] = v
        self.root.attributes("-alpha", v)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    CodexHamurabbi().run()
