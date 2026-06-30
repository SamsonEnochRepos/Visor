"""
hud.py — JARVIS-style transparent HUD overlay for VISOR.

Displays real-time gesture classification, confidence bars, intent,
voice status, FPS, and latency in a sleek always-on-top overlay.

Enhanced from the original overlay.py with:
- Confidence and stability bar visualization
- Intent display
- Latency tracking
- Cleaner JARVIS aesthetic
"""

import sys
import logging
import threading
import tkinter as tk
from typing import Dict, Any

from config import Config

logger = logging.getLogger("VISOR.ui.hud")

# --- HUD Design Constants ---
_HUD_WIDTH = 320
_HUD_HEIGHT = 190
_BG_COLOR = "#0a0a0a"
_ACCENT = "#00e5ff"      # Cyan
_GREEN = "#39ff14"        # Neon green
_ORANGE = "#ff6600"       # Warning orange
_YELLOW = "#ffff00"       # Highlight yellow
_DIM = "#555555"          # Dim text
_BAR_BG = "#1a1a1a"      # Bar background
_FONT = "Consolas"


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


class HUD:
    """JARVIS-style transparent HUD overlay.

    Displays:
    - Current gesture + confidence bar
    - Stability bar
    - Current intent
    - Voice status / last command
    - FPS + latency
    """

    def __init__(self, shared_state: Dict[str, Any],
                 visible_event: threading.Event,
                 stop_event: threading.Event) -> None:
        self._shared = shared_state
        self._visible = visible_event
        self._stop = stop_event
        self._cfg = Config.get()
        self._root: tk.Tk = None  # type: ignore
        self._labels: Dict[str, tk.Label] = {}
        self._bars: Dict[str, tk.Canvas] = {}

    def run(self) -> None:
        """Main loop — must be called from the main thread (Tkinter requirement)."""
        logger.info("HUD starting")

        try:
            self._root = tk.Tk()
            self._root.title("VISOR HUD")
            self._root.overrideredirect(True)
            self._root.attributes("-topmost", True)
            self._root.attributes("-alpha", 0.80)
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
                hwnd = (
                    int(self._root.wm_frame(), 16)
                    if self._root.wm_frame()
                    else 0
                )
                if hwnd == 0:
                    try:
                        import win32gui  # type: ignore
                        hwnd = win32gui.FindWindow(None, "VISOR HUD")
                    except Exception:
                        pass
                if hwnd:
                    _make_click_through(hwnd)

            self._build_ui()
            self._root.after(80, self._update_loop)
            logger.info("HUD started")
            self._root.mainloop()

        except Exception as exc:
            logger.error("HUD crashed: %s", exc)
        finally:
            logger.info("HUD stopped")

    def _build_ui(self) -> None:
        """Build the JARVIS-style HUD layout."""
        root = self._root

        # --- Title bar ---
        title = tk.Label(
            root, text="⬡ VISOR", font=(_FONT, 11, "bold"),
            fg=_ACCENT, bg=_BG_COLOR, anchor="w",
        )
        title.pack(fill="x", padx=10, pady=(8, 2))

        # --- Separator ---
        sep = tk.Frame(root, height=1, bg=_ACCENT)
        sep.pack(fill="x", padx=10, pady=2)

        # --- Gesture + Confidence bar ---
        self._add_bar_row(root, "gesture", "GESTURE", "none")

        # --- Stability bar ---
        self._add_bar_row(root, "stability", "STABLE", "0%")

        # --- Intent ---
        self._add_text_row(root, "intent", "INTENT", "idle")

        # --- Voice ---
        self._add_text_row(root, "voice", "VOICE", "—")

        # --- FPS ---
        self._add_text_row(root, "fps", "FPS", "—")

    def _add_text_row(self, parent: tk.Widget, key: str,
                      label_text: str, default: str) -> None:
        """Add a label + value text row."""
        frame = tk.Frame(parent, bg=_BG_COLOR)
        frame.pack(fill="x", padx=10, pady=1)

        lbl = tk.Label(
            frame, text=f"{label_text}:", font=(_FONT, 9),
            fg=_DIM, bg=_BG_COLOR, width=9, anchor="w",
        )
        lbl.pack(side="left")

        val = tk.Label(
            frame, text=default, font=(_FONT, 9, "bold"),
            fg=_GREEN, bg=_BG_COLOR, anchor="w",
        )
        val.pack(side="left", fill="x", expand=True)
        self._labels[key] = val

    def _add_bar_row(self, parent: tk.Widget, key: str,
                     label_text: str, default: str) -> None:
        """Add a label + value + progress bar row."""
        frame = tk.Frame(parent, bg=_BG_COLOR)
        frame.pack(fill="x", padx=10, pady=1)

        lbl = tk.Label(
            frame, text=f"{label_text}:", font=(_FONT, 9),
            fg=_DIM, bg=_BG_COLOR, width=9, anchor="w",
        )
        lbl.pack(side="left")

        val = tk.Label(
            frame, text=default, font=(_FONT, 9, "bold"),
            fg=_GREEN, bg=_BG_COLOR, width=12, anchor="w",
        )
        val.pack(side="left")
        self._labels[key] = val

        # Progress bar canvas
        bar_w = 80
        bar_h = 10
        canvas = tk.Canvas(
            frame, width=bar_w, height=bar_h,
            bg=_BAR_BG, highlightthickness=0,
        )
        canvas.pack(side="right", padx=(4, 0))
        # Draw empty bar
        canvas.create_rectangle(0, 0, 0, bar_h, fill=_GREEN, tags="fill")
        self._bars[key] = canvas

    def _update_bar(self, key: str, fraction: float,
                    color: str = _GREEN) -> None:
        """Update a progress bar to show a fraction (0.0 - 1.0)."""
        if key not in self._bars:
            return
        canvas = self._bars[key]
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        fill_w = max(0, min(w, int(w * fraction)))
        canvas.delete("fill")
        if fill_w > 0:
            canvas.create_rectangle(0, 0, fill_w, h, fill=color, tags="fill")

    def _update_loop(self) -> None:
        """Periodic HUD update from shared state (every 80ms)."""
        if self._stop.is_set():
            self._root.destroy()
            return

        # Toggle visibility
        if self._visible.is_set():
            self._root.deiconify()
        else:
            self._root.withdraw()

        # Read shared state
        gesture_state = self._shared.get("gesture_state", "none")
        confidence_str = self._shared.get("gesture_confidence", "0%")
        stability_str = self._shared.get("gesture_stability", "0%")
        intent = self._shared.get("intent", "idle")
        voice_status = self._shared.get("voice_status", "—")
        fps = self._shared.get("fps", "—")
        last_voice = self._shared.get("last_voice", "")

        # Parse percentages
        try:
            confidence_val = float(confidence_str.rstrip("%")) / 100.0
        except (ValueError, AttributeError):
            confidence_val = 0.0

        try:
            stability_val = float(stability_str.rstrip("%")) / 100.0
        except (ValueError, AttributeError):
            stability_val = 0.0

        # Update gesture label + bar
        self._labels["gesture"].config(text=gesture_state)
        gesture_color = _GREEN
        if gesture_state in ("grab", "pinch"):
            gesture_color = _ORANGE
        elif gesture_state in ("swipe_left", "swipe_right", "push"):
            gesture_color = _YELLOW
        elif gesture_state == "fist":
            gesture_color = "#ff3333"
        self._labels["gesture"].config(fg=gesture_color)
        self._update_bar("gesture", confidence_val, gesture_color)

        # Update stability bar
        self._labels["stability"].config(text=stability_str)
        stability_color = _GREEN if stability_val > 0.5 else _ORANGE
        self._update_bar("stability", stability_val, stability_color)

        # Update intent
        self._labels["intent"].config(text=intent)
        intent_color = _GREEN
        if intent in ("drag_start", "drag_move"):
            intent_color = _ORANGE
        elif intent in ("select", "close", "confirm"):
            intent_color = _YELLOW
        self._labels["intent"].config(fg=intent_color)

        # Update voice
        voice_text = last_voice if last_voice else voice_status
        self._labels["voice"].config(text=str(voice_text)[:28])

        # Update FPS
        self._labels["fps"].config(text=str(fps))

        self._root.after(80, self._update_loop)

    def request_stop(self) -> None:
        """Request the HUD to shut down."""
        if self._root is not None:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:
                pass
