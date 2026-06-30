"""
overlay.py - Transparent always-on-top HUD for VISOR.

Displays current gesture state, voice status, FPS, and active mode
in a semi-transparent click-through window in the bottom-right corner.
Uses Tkinter with Win32 extensions for click-through transparency.
"""

import sys
import logging
import threading
import tkinter as tk
from typing import Dict, Any

from config import Config

logger = logging.getLogger("VISOR.overlay")

# HUD dimensions
_HUD_WIDTH = 280
_HUD_HEIGHT = 150
_BG_COLOR = "#0a0a0a"
_ACCENT = "#00e5ff"
_GREEN = "#39ff14"
_DIM = "#555555"
_FONT_FAMILY = "Consolas"


def _make_click_through(hwnd: int) -> None:
    """Make a window click-through on Windows using Win32 API."""
    try:
        import win32con  # type: ignore
        import win32gui  # type: ignore

        styles = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        styles |= win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, styles)
    except ImportError:
        logger.warning("pywin32 not available — overlay won't be click-through")
    except Exception as exc:
        logger.error("Failed to set click-through: %s", exc)


class Overlay:
    """Transparent HUD overlay running in its own thread."""

    def __init__(self, shared_state: Dict[str, Any],
                 visible_event: threading.Event,
                 stop_event: threading.Event) -> None:
        self._shared = shared_state
        self._visible = visible_event
        self._stop = stop_event
        self._cfg = Config.get()
        self._root: tk.Tk = None  # type: ignore
        self._labels: Dict[str, tk.Label] = {}

    def run(self) -> None:
        """Main loop — call this from a thread. Creates Tk root and runs mainloop."""
        logger.info("Overlay starting")

        try:
            self._root = tk.Tk()
            self._root.title("VISOR HUD")
            self._root.overrideredirect(True)
            self._root.attributes("-topmost", True)
            self._root.attributes("-alpha", 0.75)
            self._root.configure(bg=_BG_COLOR)

            # Position bottom-right
            screen_w = self._root.winfo_screenwidth()
            screen_h = self._root.winfo_screenheight()
            x = screen_w - _HUD_WIDTH - 20
            y = screen_h - _HUD_HEIGHT - 60
            self._root.geometry(f"{_HUD_WIDTH}x{_HUD_HEIGHT}+{x}+{y}")

            # Make click-through on Windows
            if sys.platform == "win32":
                self._root.update_idletasks()
                hwnd = int(self._root.wm_frame(), 16) if self._root.wm_frame() else 0
                if hwnd == 0:
                    # Fallback: get HWND from title
                    try:
                        import win32gui  # type: ignore
                        hwnd = win32gui.FindWindow(None, "VISOR HUD")
                    except Exception:
                        pass
                if hwnd:
                    _make_click_through(hwnd)

            # Build HUD layout
            self._build_ui()

            # Schedule periodic updates
            self._root.after(100, self._update_loop)
            logger.info("Overlay started")
            self._root.mainloop()

        except Exception as exc:
            logger.error("Overlay crashed: %s", exc)
        finally:
            logger.info("Overlay stopped")

    def _build_ui(self) -> None:
        """Create the HUD labels."""
        root = self._root

        # Title bar
        title = tk.Label(root, text="⬡ VISOR", font=(_FONT_FAMILY, 11, "bold"),
                         fg=_ACCENT, bg=_BG_COLOR, anchor="w")
        title.pack(fill="x", padx=10, pady=(8, 2))

        # Separator
        sep = tk.Frame(root, height=1, bg=_ACCENT)
        sep.pack(fill="x", padx=10, pady=2)

        # Info rows
        row_defs = [
            ("gesture", "GESTURE", "idle"),
            ("mode", "MODE", "—"),
            ("voice", "VOICE", "—"),
            ("fps", "FPS", "—"),
        ]
        for key, label_text, default in row_defs:
            frame = tk.Frame(root, bg=_BG_COLOR)
            frame.pack(fill="x", padx=10, pady=1)
            lbl = tk.Label(frame, text=f"{label_text}:", font=(_FONT_FAMILY, 9),
                           fg=_DIM, bg=_BG_COLOR, width=9, anchor="w")
            lbl.pack(side="left")
            val = tk.Label(frame, text=default, font=(_FONT_FAMILY, 9, "bold"),
                           fg=_GREEN, bg=_BG_COLOR, anchor="w")
            val.pack(side="left", fill="x", expand=True)
            self._labels[key] = val

    def _update_loop(self) -> None:
        """Periodically update HUD from shared state."""
        if self._stop.is_set():
            self._root.destroy()
            return

        # Toggle visibility
        if self._visible.is_set():
            self._root.deiconify()
        else:
            self._root.withdraw()

        # Update values
        gesture_state = self._shared.get("gesture_state", "idle")
        voice_status = self._shared.get("voice_status", "—")
        fps = self._shared.get("fps", "—")
        gesture_status = self._shared.get("gesture_status", "—")
        last_voice = self._shared.get("last_voice", "")

        self._labels["gesture"].config(text=gesture_state)
        self._labels["mode"].config(text=gesture_status)
        self._labels["fps"].config(text=fps)

        # Voice label: show last command briefly or status
        voice_text = last_voice if last_voice else voice_status
        self._labels["voice"].config(text=voice_text[:25])

        # Color coding
        if gesture_state in ("dragging", "drag_moving", "palm_drag"):
            self._labels["gesture"].config(fg="#ff6600")
        elif gesture_state in ("clicking", "quick_select", "double_clicking"):
            self._labels["gesture"].config(fg="#ffff00")
        else:
            self._labels["gesture"].config(fg=_GREEN)

        self._root.after(100, self._update_loop)

    def request_stop(self) -> None:
        """Request the overlay to shut down."""
        if self._root is not None:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:
                pass
