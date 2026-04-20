#!/usr/bin/env python3
"""
CodexHamurabbi — Codex Desktop usage overlay for Windows.
Reads from ~/.codex/state_5.sqlite — no auth, no API calls.
Double-click header to toggle compact mode. Right-click for settings.
"""

import tkinter as tk
import json, os, subprocess, sys, threading
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

# Row definitions: (value_key, sub_key, icon, i18n_key, row_type)
# row_type: "bar" = progress bar + value, "text" = icon + value only
ROWS = [
    ("today_pct",      "today_fmt",  "📅", "row_today",    "bar"),
    ("week_pct",       "week_fmt",   "📆", "row_week",     "bar"),
    ("today_sessions", None,         "🔀", "row_sessions", "text"),
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
        self.cfg   = Settings()
        self.root  = tk.Tk()
        self._body = None
        self._rows_widgets = []
        self._build_window()
        self._build_content()
        self._fit_height()
        self._refresh_ui()
        self._schedule_bg_fetch()

    def _t(self, key: str, **kwargs) -> str:
        return i18n.get(self.cfg["lang"], key, **kwargs)

    # ── Window ────────────────────────────────────────────────────────────────
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
        y = self.cfg["pos_y"] if self.cfg["pos_y"] >= 0 else sh - 220 - 60
        r.geometry(f"{W}x200+{x}+{y}")

        r.bind("<Button-1>",        self._drag_start)
        r.bind("<B1-Motion>",       self._drag_move)
        r.bind("<ButtonRelease-1>", self._drag_end)
        r.bind("<Button-3>",        self._ctx_menu)

        # Header
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

        btn = tk.Label(hdr, text="↺", bg=C["hdr"], fg=C["muted"],
                       font=("Segoe UI", 11), cursor="hand2")
        btn.pack(side="right", padx=1)
        btn.bind("<Button-1>", lambda _: self._bg_fetch())
        btn.bind("<Enter>",    lambda _: btn.config(fg=C["accent"]))
        btn.bind("<Leave>",    lambda _: btn.config(fg=C["muted"]))

        self._upd_var = tk.StringVar(value="")
        tk.Label(hdr, textvariable=self._upd_var,
                 bg=C["hdr"], fg=C["muted"],
                 font=("Segoe UI", 7)).pack(side="right", padx=3)

    # ── Content ───────────────────────────────────────────────────────────────
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
        for val_key, sub_key, icon, name_key, row_type in ROWS:
            name = i18n.get(lang, name_key)
            if compact:
                w = self._make_compact_row(self._body, icon)
            else:
                w = self._make_full_row(self._body, icon, name, row_type)
            self._rows_widgets.append(w)

        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.root.geometry(f"{W}x1+{x}+{y}")

    def _make_full_row(self, parent, icon: str, name: str,
                       row_type: str) -> dict:
        f = tk.Frame(parent, bg=C["bg"])
        f.pack(fill="x", pady=1)

        tk.Label(f, text=f"{icon} {name}", bg=C["bg"], fg=C["muted"],
                 font=("Segoe UI", 7), width=12, anchor="w").pack(side="left")

        if row_type == "bar":
            canvas = tk.Canvas(f, height=5, bg=C["bar"],
                               highlightthickness=0, bd=0, width=62)
            canvas.pack(side="left", padx=(2, 3))
        else:
            canvas = None
            tk.Frame(f, width=67, bg=C["bg"]).pack(side="left")  # spacer

        val_var = tk.StringVar(value="—")
        val_lbl = tk.Label(f, textvariable=val_var, bg=C["bg"], fg=C["text"],
                           font=("Segoe UI", 7), anchor="e",
                           width=6 if row_type == "bar" else 10)
        val_lbl.pack(side="left")

        return {"mode": "full", "canvas": canvas, "row_type": row_type,
                "val_var": val_var, "val_lbl": val_lbl}

    def _make_compact_row(self, parent, icon: str) -> dict:
        f = tk.Frame(parent, bg=C["bg"])
        f.pack(fill="x", pady=1)

        tk.Label(f, text=icon, bg=C["bg"], fg=C["muted"],
                 font=("Segoe UI", 8), width=2).pack(side="left")

        val_var = tk.StringVar(value="—")
        val_lbl = tk.Label(f, textvariable=val_var, bg=C["bg"], fg=C["text"],
                           font=("Segoe UI", 8, "bold"), width=7, anchor="e")
        val_lbl.pack(side="left")

        return {"mode": "compact", "canvas": None, "row_type": "text",
                "val_var": val_var, "val_lbl": val_lbl}

    def _draw_bar(self, canvas: tk.Canvas, pct: float, color: str):
        if canvas is None:
            return
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

        for i, (val_key, sub_key, icon, name_key, row_type) in enumerate(ROWS):
            w = self._rows_widgets[i]

            raw = cache.get(val_key, 0)

            if row_type == "bar":
                pct = float(raw or 0)
                color = bar_color(pct)
                display = str(cache.get(sub_key, "—"))
                w["val_lbl"].config(fg=pct_color(pct))
                self.root.after(30 * i,
                    lambda c=w["canvas"], p=pct, col=color:
                        self._draw_bar(c, p, col))
            else:
                # text row
                if val_key == "today_sessions":
                    sfx = self._t("sessions_sfx")
                    display = f"{raw} {sfx}" if raw else "—"
                elif val_key == "last_model":
                    # shorten model name
                    m = str(raw or "—")
                    display = m.replace("gpt-5.4-mini", "GPT-5 mini") \
                               .replace("gpt-5.4", "GPT-5") \
                               .replace("gpt-4", "GPT-4")
                else:
                    display = str(raw) if raw else "—"

                w["val_lbl"].config(fg=C["text"])

            w["val_var"].set(display)

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

    def _schedule_bg_fetch(self):
        self._bg_fetch()
        ms = max(self.cfg["interval"] * 1000, 60_000)
        self.root.after(ms, self._schedule_bg_fetch)

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
        m.add_command(label=self._t("menu_refresh"), command=self._bg_fetch)
        m.add_separator()

        sub = tk.Menu(m, tearoff=0, bg=C["bg2"], fg=C["text"],
                      activebackground=C["accent"], font=("Segoe UI", 9))
        for v, key in [(60, "int_1m"), (300, "int_5m"),
                       (600, "int_10m"), (1800, "int_30m")]:
            mark = "✓  " if self.cfg["interval"] == v else "    "
            sub.add_command(label=f"{mark}{self._t(key)}",
                            command=lambda v=v: self._set_interval(v))
        m.add_cascade(label=self._t("menu_interval"), menu=sub)

        sub2 = tk.Menu(m, tearoff=0, bg=C["bg2"], fg=C["text"],
                       activebackground=C["accent"], font=("Segoe UI", 9))
        for a in (1.0, 0.92, 0.80, 0.60):
            mark = "✓  " if abs(self.cfg["opacity"] - a) < 0.01 else "    "
            sub2.add_command(label=f"{mark}{int(a * 100)}%",
                             command=lambda a=a: self._set_opacity(a))
        m.add_cascade(label=self._t("menu_opacity"), menu=sub2)

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

    def _set_interval(self, v): self.cfg["interval"] = v
    def _set_opacity(self, v):
        self.cfg["opacity"] = v
        self.root.attributes("-alpha", v)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    CodexHamurabbi().run()
