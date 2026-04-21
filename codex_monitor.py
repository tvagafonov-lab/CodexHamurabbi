#!/usr/bin/env python3
"""
CodexHamurabbi — Codex Desktop usage overlay for Windows.
Reads rate limits from ~/.codex/sessions JSONL — no auth, no API calls.
Double-click header to toggle compact mode. Right-click for settings.
"""

import tkinter as tk
import ctypes
from ctypes import wintypes
import json, os, time, threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
import i18n
import fetch_codex

# Tray-mode deps are optional — the overlay works fully without them if the
# user never enables tray. Import lazily but eagerly-try here so the menu
# item can hide itself cleanly when the libs aren't installed.
try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except Exception:
    TRAY_AVAILABLE = False

# Win32 indices for GetSystemMetrics / SystemParametersInfo.
SM_XVIRTUALSCREEN  = 76
SM_YVIRTUALSCREEN  = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SPI_GETWORKAREA    = 0x0030

# ── Paths ─────────────────────────────────────────────────────────────────────
CODEX_HOME    = Path(os.environ.get("USERPROFILE", Path.home())) / ".codex"
CACHE_FILE    = CODEX_HOME / "hamurabbi_cache.json"
SETTINGS_FILE = CODEX_HOME / "hamurabbi_settings.json"

DEFAULT_SETTINGS = {
    "opacity":        0.92,
    "compact":        False,
    "dock":           False,
    "tray":           False,   # fourth mode: hidden overlay, system-tray icon
    "dock_x":         -1,      # saved X in dock mode (-1 = default near Start)
    "lang":           "en",
    "show_remaining": True,    # True = remaining %, False = used %
    "pos_x":          -1,
    "pos_y":          -1,
}

# ── Colors — Codex violet on deep space ───────────────────────────────────────
C = {
    "bg":     "#0a0818",
    "bg2":    "#141025",
    "hdr":    "#1c1630",
    "accent": "#a78bfa",   # Codex violet
    "text":   "#e9e2ff",   # pale lavender
    "muted":  "#6a5a8a",
    "green":  "#7ecf6e",
    "yellow": "#e8a020",
    "red":    "#e06050",
    "bar":    "#2a2040",
}

W_FULL    = 265
W_COMPACT = 165

RING_SIZE           = 36   # ring canvas size (px) in dock mode
RING_PAD            = 3    # padding around each ring canvas
# +2 nudges the strip to 44 px total, matching the Win11 taskbar height so the
# dock sits flush above it without a hairline gap.
DOCK_H              = RING_SIZE + RING_PAD * 2 + 2
DOCK_DEFAULT_X      = 80   # default dock X near the Win11 Start button
TASKBAR_FALLBACK_H  = 48   # assumed taskbar height if SPI_GETWORKAREA fails
FALLBACK_FETCH_MS   = 180_000   # re-fetch every 3 min even when nothing changed
TRAY_ICON_SIZE      = 32        # drawn size; Windows downscales to 16×16
TRAY_OUTER_WIDTH    = 4         # outer ring stroke (5h)
TRAY_INNER_WIDTH    = 3         # inner ring stroke (week)


# ── Multi-monitor helpers ─────────────────────────────────────────────────────
def _virtual_screen_rect() -> "tuple[int, int, int, int] | None":
    """Bounding box of all currently connected monitors, in screen coords."""
    try:
        u = ctypes.windll.user32
        x = u.GetSystemMetrics(SM_XVIRTUALSCREEN)
        y = u.GetSystemMetrics(SM_YVIRTUALSCREEN)
        w = u.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        h = u.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        return (x, y, x + w, y + h)
    except Exception:
        return None


def _rect_on_screen(x: int, y: int, w: int, h: int, min_overlap: int = 40) -> bool:
    """True if the window rect overlaps the visible virtual desktop enough to be
    reachable — used to detect positions stranded on a now-disconnected monitor."""
    vs = _virtual_screen_rect()
    if vs is None:
        return True  # can't probe → assume OK
    vl, vt, vr, vb = vs
    return (min(x + w, vr) - max(x, vl) >= min_overlap
            and min(y + h, vb) - max(y, vt) >= min_overlap)


ROWS = [
    # (pct_key, reset_key, icon, label_key, window_seconds)
    ("fh_pct", "fh_reset", "⏱", "row_5h",      5 * 3600),
    ("wd_pct", "wd_reset", "📅", "row_week",   7 * 86400),
    ("cr_pct", None,       "💳", "row_credits", 0),
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
_cache_data: dict = {}
_cache_mtime: float = 0.0


def read_cache() -> dict:
    """Return the parsed cache file, re-reading only when its mtime changes."""
    global _cache_data, _cache_mtime
    try:
        mt = os.path.getmtime(CACHE_FILE)
    except OSError:
        return _cache_data
    if mt != _cache_mtime:
        try:
            _cache_data  = json.loads(CACHE_FILE.read_text("utf-8"))
            _cache_mtime = mt
        except (OSError, ValueError):
            pass
    return _cache_data


def _reset_dt(unix_ts: int | None) -> "datetime | None":
    """Parse a Unix timestamp into an aware UTC datetime, or None if invalid."""
    if unix_ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(unix_ts), tz=timezone.utc)
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def reset_passed(unix_ts: int | None, now: "datetime | None" = None) -> bool:
    """True if the given Unix timestamp is in the past."""
    dt = _reset_dt(unix_ts)
    if dt is None:
        return False
    return dt < (now or datetime.now(tz=timezone.utc))


def fmt_reset(unix_ts: int | None, lang: str, window_seconds: int = 0,
              now: "datetime | None" = None) -> str:
    """Format a Unix int timestamp into a human-readable countdown.

    When the cached timestamp is in the past and `window_seconds` is known,
    roll it forward by whole windows so we show the *next* reset instead of a
    stale "reset" label (Codex only refreshes resets_at on new token_count).
    """
    tr = i18n.STRINGS.get(lang, i18n.STRINGS["en"])
    dt = _reset_dt(unix_ts)
    if dt is None:
        return "—"
    if now is None:
        now = datetime.now(tz=timezone.utc)
    diff = dt - now
    if diff.total_seconds() < 0:
        if window_seconds <= 0:
            return tr["reset_done"]
        cycles = int(-diff.total_seconds() // window_seconds) + 1
        dt     = dt + timedelta(seconds=window_seconds * cycles)
        diff   = dt - now
    mins = int(diff.total_seconds() // 60)
    h, m = divmod(mins, 60)
    if diff.total_seconds() < 86400:
        return f"{h}h {m:02}m" if h else f"{m}m"
    local = dt.astimezone()
    return f"{tr['days'][local.weekday()]} {local.strftime('%H:%M')}"


def bar_color(pct: float) -> str:
    if pct >= 90: return C["red"]
    if pct >= 60: return C["yellow"]
    return C["green"]


def pct_color(pct: float) -> str:
    if pct >= 90: return C["red"]
    if pct >= 60: return C["yellow"]
    return C["text"]


def _pil_color(hex_str: str) -> tuple:
    """Convert a '#rrggbb' string to an opaque RGBA tuple for Pillow."""
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)


def resolve_row(cache: dict, key_pct: str, key_rst: "str | None",
                win_s: int, lang: str, now: "datetime") -> tuple:
    """Compute (pct, rst_txt, credits_tuple_or_None) for a ROWS entry.
    Shared by _refresh_ui, _build_tray_tooltip and _show_hover_card so the
    stale-reset-zeroing and credits formatting live in one place."""
    if key_rst is None:  # Credits row
        used  = cache.get("cr_used",  0)
        limit = cache.get("cr_limit", 0)
        curr  = "€" if cache.get("cr_curr") == "EUR" else cache.get("cr_curr", "")
        rst_txt = f"{used:.2f} / {limit:.2f} {curr}".rstrip()
        return (float(cache.get(key_pct, 0)), rst_txt, (used, limit, curr))
    pct = float(cache.get(key_pct, 0))
    if reset_passed(cache.get(key_rst), now):
        pct = 0.0
    return (pct, fmt_reset(cache.get(key_rst), lang, win_s, now), None)


# ── Main window ───────────────────────────────────────────────────────────────
class CodexHamurabbi:
    def __init__(self):
        self.cfg              = Settings()
        self.root             = tk.Tk()
        self._body            = None
        self._rows_widgets    = []
        self._known_mtime     = 0.0   # latest mtime seen across all session files
        self._last_fetch_time = 0.0
        self._refresh_id      = None
        self._tray_icon       = None
        self._hover_card      = None
        self._hover_hide_id   = None
        self._last_pct_5h     = 0.0
        self._last_pct_wk     = 0.0
        self._build_window()
        self._build_content()
        self._fit_height()
        self._refresh_ui()
        self._schedule_bg_fetch()
        # Honor persisted tray state — enter tray AFTER first _refresh_ui so
        # the initial icon reflects current cache values.
        if self.cfg["tray"] and TRAY_AVAILABLE:
            self.root.after(200, self._enter_tray)

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
        if not _rect_on_screen(x, y, W, 200):
            x, y = sw - W - 20, sh - 200 - 60  # stranded on a gone monitor
        r.geometry(f"{W}x200+{x}+{y}")

        r.bind("<Button-1>",        self._drag_start)
        r.bind("<B1-Motion>",       self._drag_move)
        r.bind("<ButtonRelease-1>", self._drag_end)
        r.bind("<Button-3>",        self._ctx_menu)
        r.bind("<Double-Button-1>", self._on_double_click)

        # Header — hidden in dock mode; packed/unpacked by _build_content
        self._hdr = tk.Frame(r, bg=C["hdr"], height=24)
        self._hdr.pack_propagate(False)

        self._title_var = tk.StringVar(value="◆ CodexHamurabbi")
        hdr_lbl = tk.Label(self._hdr, textvariable=self._title_var,
                           bg=C["hdr"], fg=C["accent"],
                           font=("Segoe UI", 8, "bold"), cursor="hand2")
        hdr_lbl.pack(side="left", padx=7)
        hdr_lbl.bind("<Double-Button-1>", lambda _: self._toggle_compact())

        x_lbl = tk.Label(self._hdr, text="✕", bg=C["hdr"], fg=C["muted"],
                         font=("Segoe UI", 10), cursor="hand2")
        x_lbl.pack(side="right", padx=5)
        x_lbl.bind("<Button-1>", lambda _: r.destroy())
        x_lbl.bind("<Enter>",    lambda _: x_lbl.config(fg=C["red"]))
        x_lbl.bind("<Leave>",    lambda _: x_lbl.config(fg=C["muted"]))

        self._upd_var = tk.StringVar(value="")
        tk.Label(self._hdr, textvariable=self._upd_var,
                 bg=C["hdr"], fg=C["muted"],
                 font=("Segoe UI", 7)).pack(side="right", padx=3)

    # ── Content (rebuilt on mode / language change) ───────────────────────────
    def _build_content(self):
        self._hdr.pack_forget()
        if self._body:
            self._body.destroy()

        if self.cfg["dock"]:
            self._build_dock()
            return

        self._hdr.pack(fill="x")
        compact = self.cfg["compact"]
        lang    = self.cfg["lang"]
        W       = W_COMPACT if compact else W_FULL

        self._title_var.set("◆ Codex" if compact else "◆ CodexHamurabbi")

        self._body = tk.Frame(self.root, bg=C["bg"],
                              padx=6 if compact else 10)
        self._body.pack(fill="x", pady=(4, 5))

        self._rows_widgets = []
        for key_pct, key_rst, icon, name_key, _win_s in ROWS:
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
        if self.cfg["dock"]:
            return  # geometry fixed by _build_dock
        self.root.update_idletasks()
        h = self.root.winfo_reqheight()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        W = W_COMPACT if self.cfg["compact"] else W_FULL
        self.root.geometry(f"{W}x{h}+{x}+{y}")

    # ── Data refresh ──────────────────────────────────────────────────────────
    def _refresh_ui(self):
        cache = read_cache()
        lang  = self.cfg["lang"]
        now   = datetime.now(tz=timezone.utc)   # single snapshot for the whole tick

        for i, (key_pct, key_rst, icon, name_key, win_s) in enumerate(ROWS):
            pct, rst_txt, _ = resolve_row(cache, key_pct, key_rst, win_s, lang, now)
            if   key_pct == "fh_pct": self._last_pct_5h = pct
            elif key_pct == "wd_pct": self._last_pct_wk = pct
            color = bar_color(pct)
            w     = self._rows_widgets[i]

            if w["mode"] == "dock":
                self.root.after(30 * i, lambda c=w["canvas"], p=pct, col=color:
                                self._draw_ring(c, p, col))
            else:
                display_pct = max(0.0, 100.0 - pct) if self.cfg["show_remaining"] else pct
                w["pct_var"].set(f"{display_pct:.0f}%")
                w["pct_lbl"].config(fg=pct_color(pct))  # color always by used %
                w["rst_var"].set(rst_txt)
                if w["mode"] == "full":
                    self.root.after(30 * i, lambda c=w["canvas"], p=pct, col=color:
                                    self._draw_bar(c, p, col))

        if cache.get("fetched_at"):
            try:
                dt = datetime.fromisoformat(cache["fetched_at"])
                self._upd_var.set(f"⟳ {dt.astimezone().strftime('%H:%M')}")
            except (ValueError, TypeError):
                pass

        self._reclaim_if_offscreen()

        if self.cfg["tray"] and self._tray_icon is not None:
            self._update_tray(cache, now)

        if self._refresh_id is not None:
            self.root.after_cancel(self._refresh_id)
        self._refresh_id = self.root.after(10_000, self._refresh_ui)

    def _reclaim_if_offscreen(self):
        """If a monitor was disconnected and our window is stranded, snap it back."""
        try:
            x, y = self.root.winfo_x(), self.root.winfo_y()
            w, h = self.root.winfo_width(), self.root.winfo_height()
        except Exception:
            return
        # Before Tk finishes laying out, winfo_width/height can return 1 from
        # the intermediate `{W}x1+{x}+{y}` set in _build_content. Reposition
        # decisions made then would put the window at nonsense coordinates.
        if w < 50 or h < 40:
            return
        if _rect_on_screen(x, y, w, h):
            return
        if self.cfg["dock"]:
            dw = self._dock_width()
            nx, ny = self._dock_snap_pos(dw, DOCK_H)
            self.root.geometry(f"{dw}x{DOCK_H}+{nx}+{ny}")
        else:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            W = W_COMPACT if self.cfg["compact"] else W_FULL
            # Center on the primary monitor — a deterministic visible spot
            # that survives DPI changes and monitor reshuffling.
            self.root.geometry(f"+{(sw - W) // 2}+{(sh - h) // 2}")

    # ── Mode & language ───────────────────────────────────────────────────────
    def _rebuild_ui(self):
        self._build_content()
        self.root.after(50, self._fit_height)
        self._refresh_ui()

    def _toggle_compact(self):
        self.cfg["compact"] = not self.cfg["compact"]
        self._rebuild_ui()

    def _set_lang(self, lang: str):
        self.cfg["lang"] = lang
        self._rebuild_ui()

    def _on_double_click(self, e):
        if self.cfg["dock"]:
            self._toggle_dock()
        # non-dock: header label handles its own double-click → toggle compact

    def _toggle_dock(self):
        self.cfg["dock"] = not self.cfg["dock"]
        self._rebuild_ui()

    # ── Dock mode helpers ─────────────────────────────────────────────────────
    def _dock_width(self) -> int:
        return len(ROWS) * (RING_SIZE + RING_PAD * 2) + 4

    def _dock_snap_pos(self, w: int, h: int) -> tuple:
        """Y: just above primary-monitor taskbar. X: saved dock_x, or a small
        default near the Start button. Falls back to the primary monitor if
        the saved X is stranded on a disconnected monitor."""
        try:
            wa = wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(
                SPI_GETWORKAREA, 0, ctypes.byref(wa), 0)
            y = wa.bottom - h
        except Exception:
            y = self.root.winfo_screenheight() - h - TASKBAR_FALLBACK_H
        x = self.cfg["dock_x"] if self.cfg["dock_x"] >= 0 else DOCK_DEFAULT_X
        if not _rect_on_screen(x, y, w, h):
            x = DOCK_DEFAULT_X
        return x, y

    def _build_dock(self):
        """Build the dock strip: one ring canvas per row, no header."""
        self._body = tk.Frame(self.root, bg=C["bg"])
        self._body.pack(fill="both", expand=True)
        self._rows_widgets = []
        for _key_pct, _key_rst, _icon, _name_key, _win_s in ROWS:
            c = tk.Canvas(self._body, width=RING_SIZE, height=RING_SIZE,
                          bg=C["bg"], highlightthickness=0, bd=0)
            c.pack(side="left", padx=RING_PAD, pady=RING_PAD)
            self._rows_widgets.append({"mode": "dock", "canvas": c})
        dw = self._dock_width()
        dx, dy = self._dock_snap_pos(dw, DOCK_H)
        self.root.geometry(f"{dw}x{DOCK_H}+{dx}+{dy}")

    def _draw_ring(self, canvas: tk.Canvas, pct: float, color: str):
        """Draw a donut-ring progress indicator on canvas."""
        canvas.delete("all")
        s, p = RING_SIZE, 3
        display_pct = max(0.0, 100.0 - pct) if self.cfg["show_remaining"] else pct
        # Background ring (full circle)
        canvas.create_arc(p, p, s - p, s - p,
                          start=90, extent=-359.9,
                          style="arc", width=4, outline=C["bar"])
        # Progress arc
        if display_pct > 0:
            canvas.create_arc(p, p, s - p, s - p,
                              start=90,
                              extent=-min(max(1.0, 3.6 * display_pct), 359.9),
                              style="arc", width=4, outline=color)
        # Center percentage text
        canvas.create_text(s // 2, s // 2,
                           text=f"{display_pct:.0f}",
                           fill=color if pct >= 1 else C["muted"],
                           font=("Segoe UI", 7, "bold"))

    # ── Background fetch ──────────────────────────────────────────────────────
    def _bg_fetch(self):
        def run():
            err = None
            try:
                fetch_codex.fetch_and_save()
            except Exception as e:
                err = type(e).__name__
            # Keep _known_mtime in sync so the next watcher tick doesn't
            # decide the content we just pulled is already stale.
            try:
                self._known_mtime = self._find_latest_session_mtime()
            except Exception:
                pass
            if err:
                self.root.after(0, lambda: self._upd_var.set(f"⚠ {err}"))
            else:
                self.root.after(0, self._refresh_ui)
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
        """Every 2 s: scan session files in a background thread; re-fetch if changed.
        In-process fetch runs in ~500 ms, so a 3 s cooldown is plenty."""
        def check():
            try:
                mt = self._find_latest_session_mtime()
                if mt > self._known_mtime:
                    now = time.time()
                    if now - self._last_fetch_time >= 3:
                        self._known_mtime     = mt
                        self._last_fetch_time = now
                        self.root.after(0, self._bg_fetch)
            except Exception:
                pass
        threading.Thread(target=check, daemon=True).start()
        self.root.after(2_000, self._watch_sessions)

    def _schedule_bg_fetch(self):
        """Initial fetch on startup, then hand off to file watcher.
        `_bg_fetch`'s thread also primes `_known_mtime`, so we don't need a
        separate warm-up — and first paint isn't blocked by the disk walk."""
        self._bg_fetch()
        self.root.after(2_000, self._watch_sessions)
        self.root.after(FALLBACK_FETCH_MS, self._periodic_fallback)

    def _periodic_fallback(self):
        """Force a fetch every few minutes even when no Codex activity — keeps
        the `⟳` timestamp fresh and catches any file changes the mtime watcher
        may have missed. Skipped if the watcher already fetched recently."""
        if time.time() - self._last_fetch_time >= 120:
            self._last_fetch_time = time.time()
            self._bg_fetch()
        self.root.after(FALLBACK_FETCH_MS, self._periodic_fallback)

    # ── Tray mode ─────────────────────────────────────────────────────────────
    def _toggle_tray(self):
        if not TRAY_AVAILABLE:
            return
        if self.cfg["tray"]:
            self._exit_tray()
        else:
            self._enter_tray()

    def _enter_tray(self):
        """Hide the overlay, spawn a system-tray icon with two progress rings.
        On any rendering/pystray failure we abort cleanly back to overlay mode
        — otherwise the window would be withdrawn with no tray to control it."""
        try:
            icon_img = self._build_tray_image(self._last_pct_5h, self._last_pct_wk)
            tooltip  = self._build_tray_tooltip()
        except Exception:
            return
        self.cfg["tray"] = True
        self.root.withdraw()

        def on_show(icon, item):    self.root.after(0, self._show_hover_card)
        def on_restore(icon, item): self.root.after(0, self._exit_tray)
        def on_quit(icon, item):
            # Pressing Quit from the tray means "don't come back" — clear the
            # persisted tray flag so the next launch opens the overlay normally.
            self.cfg["tray"] = False
            try: icon.stop()
            except Exception: pass
            self.root.after(0, self._quit_from_tray)

        menu = pystray.Menu(
            pystray.MenuItem(self._t("menu_tray"), on_show, default=True, visible=False),
            pystray.MenuItem(self._t("menu_exit_tray"), on_restore),
            pystray.MenuItem(self._t("menu_close"), on_quit),
        )
        self._tray_icon = pystray.Icon("CodexHamurabbi", icon_img,
                                       title=tooltip, menu=menu)
        self._last_tray_pcts = (self._last_pct_5h, self._last_pct_wk)
        self._tray_icon.run_detached()

    def _quit_from_tray(self):
        self._hide_hover_card()  # cancel any pending after() callback
        try: self.root.destroy()
        except Exception: pass

    def _exit_tray(self):
        """Kill tray icon, close hover-card, bring the overlay back."""
        self.cfg["tray"] = False
        if self._tray_icon is not None:
            try: self._tray_icon.stop()
            except Exception: pass
            self._tray_icon = None
        self._hide_hover_card()
        self.root.deiconify()

    def _update_tray(self, cache: dict, now: "datetime"):
        """Refresh the tray icon bitmap and tooltip. Called from _refresh_ui.
        Skip the full redraw when neither percentage moved — icon/tooltip are
        identical, Shell_NotifyIcon(NIM_MODIFY) is a no-op we can save."""
        if self._tray_icon is None:
            return
        pcts = (self._last_pct_5h, self._last_pct_wk)
        if pcts == getattr(self, "_last_tray_pcts", None):
            self._tray_icon.title = self._build_tray_tooltip(cache, now)
            return
        try:
            self._tray_icon.icon  = self._build_tray_image(*pcts)
            self._tray_icon.title = self._build_tray_tooltip(cache, now)
            self._last_tray_pcts  = pcts
        except Exception:
            pass  # pystray/PIL error: don't break refresh_ui

    def _build_tray_image(self, pct_5h: float, pct_wk: float):
        """Render a 32x32 RGBA icon: outer arc = 5h, inner arc = week, center
        disc in brand accent color. Windows downscales to 16x16 for the tray."""
        s = TRAY_ICON_SIZE
        img   = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d     = ImageDraw.Draw(img)
        track = (60, 55, 85, 255)   # muted violet-grey track

        def arc(bbox, pct, color, width):
            d.ellipse(bbox, outline=track, width=width)
            span = max(1.0, min(359.9, 3.6 * max(0.0, min(100.0, pct))))
            d.arc(bbox, start=-90, end=-90 + span, fill=color, width=width)

        arc((1, 1, s - 2, s - 2), pct_5h, _pil_color(bar_color(pct_5h)),
            TRAY_OUTER_WIDTH)
        arc((8, 8, s - 9, s - 9), pct_wk, _pil_color(bar_color(pct_wk)),
            TRAY_INNER_WIDTH)
        d.ellipse((12, 12, s - 12, s - 12), fill=_pil_color(C["accent"]))
        return img

    def _build_tray_tooltip(self, cache: "dict | None" = None,
                            now: "datetime | None" = None) -> str:
        """Short multi-line text shown as the native Windows tray tooltip.
        Reuse cache + `now` from the refresh tick when available."""
        if cache is None: cache = read_cache()
        if now   is None: now   = datetime.now(tz=timezone.utc)
        lang  = self.cfg["lang"]
        lines = ["CodexHamurabbi"]
        for key_pct, key_rst, icon, name_key, win_s in ROWS:
            pct, rst_txt, credits = resolve_row(cache, key_pct, key_rst,
                                                win_s, lang, now)
            name = i18n.get(lang, name_key)
            if credits is not None:
                lines.append(f"{icon} {name}: {rst_txt}")
            else:
                lines.append(f"{icon} {name}: {pct:.0f}%   {rst_txt}")
        return "\n".join(lines)[:127]   # Win32 tooltip hard cap

    def _show_hover_card(self):
        """Compact Toplevel popup shown on tray left-click. Closes on FocusOut
        or after an 8 s fallback timeout."""
        self._hide_hover_card()
        card = tk.Toplevel(self.root)
        card.overrideredirect(True)
        card.attributes("-topmost", True)
        card.attributes("-alpha", self.cfg["opacity"])
        card.configure(bg=C["bg"])

        hdr = tk.Frame(card, bg=C["hdr"])
        hdr.pack(fill="x")
        tk.Label(hdr, text="◆ CodexHamurabbi", bg=C["hdr"], fg=C["accent"],
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=7, pady=3)
        close = tk.Label(hdr, text="✕", bg=C["hdr"], fg=C["muted"],
                         font=("Segoe UI", 9), cursor="hand2")
        close.pack(side="right", padx=5)
        close.bind("<Button-1>", lambda _: self._hide_hover_card())

        body = tk.Frame(card, bg=C["bg"], padx=10, pady=6)
        body.pack(fill="x")
        cache = read_cache()
        lang  = self.cfg["lang"]
        now   = datetime.now(tz=timezone.utc)
        for key_pct, key_rst, icon, name_key, win_s in ROWS:
            pct, rst_txt, credits = resolve_row(cache, key_pct, key_rst,
                                                win_s, lang, now)
            row = tk.Frame(body, bg=C["bg"])
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"{icon} {i18n.get(lang, name_key)}",
                     bg=C["bg"], fg=C["muted"], font=("Segoe UI", 8),
                     width=12, anchor="w").pack(side="left")
            if credits is not None:
                tk.Label(row, text=rst_txt, bg=C["bg"], fg=C["text"],
                         font=("Segoe UI", 8)).pack(side="left")
            else:
                tk.Label(row, text=f"{pct:.0f}%", bg=C["bg"], fg=pct_color(pct),
                         font=("Segoe UI", 8, "bold"), width=5, anchor="e"
                         ).pack(side="left")
                tk.Label(row, text=rst_txt, bg=C["bg"], fg=C["muted"],
                         font=("Segoe UI", 8)).pack(side="left", padx=(6, 0))

        card.update_idletasks()
        w, h = card.winfo_reqwidth(), card.winfo_reqheight()
        # Anchor near the cursor but keep the card fully on-screen.
        cx, cy = self.root.winfo_pointerx(), self.root.winfo_pointery()
        x, y = cx - w // 2, cy - h - 12
        vs = _virtual_screen_rect() or (0, 0, 1920, 1080)
        vl, vt, vr, vb = vs
        x = max(vl + 4, min(x, vr - w - 4))
        y = max(vt + 4, min(y, vb - h - 4))
        card.geometry(f"{w}x{h}+{x}+{y}")
        card.bind("<FocusOut>",   lambda _: self._hide_hover_card())
        card.bind("<Button-1>",   lambda _: self._hide_hover_card())
        card.focus_force()
        self._hover_card    = card
        self._hover_hide_id = self.root.after(8_000, self._hide_hover_card)

    def _hide_hover_card(self):
        if self._hover_hide_id is not None:
            try: self.root.after_cancel(self._hover_hide_id)
            except Exception: pass
            self._hover_hide_id = None
        if self._hover_card is not None:
            try: self._hover_card.destroy()
            except Exception: pass
            self._hover_card = None

    # ── Drag ──────────────────────────────────────────────────────────────────
    def _drag_start(self, e): self._ox, self._oy = e.x, e.y
    def _drag_move(self, e):
        x = self.root.winfo_x() + e.x - self._ox
        y = self.root.winfo_y() + e.y - self._oy
        self.root.geometry(f"+{x}+{y}")
    def _drag_end(self, e):
        if self.cfg["dock"]:
            self.cfg["dock_x"] = self.root.winfo_x()
        else:
            self.cfg["pos_x"] = self.root.winfo_x()
            self.cfg["pos_y"] = self.root.winfo_y()

    # ── Context menu ──────────────────────────────────────────────────────────
    def _ctx_menu(self, e):
        lang = self.cfg["lang"]
        m = tk.Menu(self.root, tearoff=0, bg=C["bg2"], fg=C["text"],
                    activebackground=C["accent"], font=("Segoe UI", 9), bd=0)

        if self.cfg["dock"]:
            m.add_command(label=self._t("menu_exit_dock"), command=self._toggle_dock)
        else:
            mode_key = "menu_full" if self.cfg["compact"] else "menu_compact"
            m.add_command(label=self._t(mode_key), command=self._toggle_compact)
            m.add_command(label=self._t("menu_dock"), command=self._toggle_dock)
            if TRAY_AVAILABLE:
                m.add_command(label=self._t("menu_tray"), command=self._toggle_tray)

        pct_key = "menu_show_used" if self.cfg["show_remaining"] else "menu_show_remaining"
        m.add_command(label=self._t(pct_key), command=self._toggle_show_remaining)
        m.add_separator()

        opacity_items = [(a, f"{int(a * 100)}%") for a in (1.0, 0.92, 0.80, 0.60)]
        m.add_cascade(label=self._t("menu_opacity"),
                      menu=self._submenu(m, opacity_items, self.cfg["opacity"],
                                         self._set_opacity,
                                         eq=lambda a, b: abs(a - b) < 0.01))

        lang_items = list(i18n.LANGUAGES.items())
        m.add_cascade(label=self._t("menu_language"),
                      menu=self._submenu(m, lang_items, lang, self._set_lang))

        m.add_separator()
        m.add_command(label=self._t("menu_close"), command=self.root.destroy)
        m.post(e.x_root, e.y_root)

    def _submenu(self, parent, items, current, on_select, eq=None):
        """Build a submenu with a ✓-prefix on the item matching `current`."""
        sub = tk.Menu(parent, tearoff=0, bg=C["bg2"], fg=C["text"],
                      activebackground=C["accent"], font=("Segoe UI", 9))
        match = eq or (lambda a, b: a == b)
        for value, label in items:
            mark = "✓  " if match(current, value) else "    "
            sub.add_command(label=f"{mark}{label}",
                            command=lambda v=value: on_select(v))
        return sub

    def _toggle_show_remaining(self):
        self.cfg["show_remaining"] = not self.cfg["show_remaining"]
        self._refresh_ui()

    def _set_opacity(self, v):
        self.cfg["opacity"] = v
        self.root.attributes("-alpha", v)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    CodexHamurabbi().run()
