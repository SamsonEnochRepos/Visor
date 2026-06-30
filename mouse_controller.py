"""
mouse_controller.py — OS input controller for VISOR.

Wraps PyAutoGUI and pynput for cursor movement, clicks, scrolling,
keyboard shortcuts, and drag operations. Uses double exponential smoothing,
dead zone, and pointer acceleration for precise control.
Maps hand landmarks to the active monitor region (or unified virtual desktop).
"""

import time
import math
import logging
import threading
from typing import Optional, Tuple, List

import pyautogui
import numpy as np

from config import Config
from monitor import MonitorInfo, VirtualDesktop, detect_monitors, get_mapping_region, print_monitor_info

logger = logging.getLogger("VISOR.mouse")

# Disable PyAutoGUI fail-safe (moving to corner throws exception)
pyautogui.FAILSAFE = False
# Remove the default pause between pyautogui calls for lower latency
pyautogui.PAUSE = 0.0


class MouseController:
    """High-level mouse/keyboard controller with smoothing and cooldowns.

    All public methods are safe to call from any thread — internal state
    is protected by a lock.
    """

    def __init__(self) -> None:
        self._cfg = Config.get()
        self._lock = threading.Lock()

        # --- FIX 1: Dual monitor detection ---
        self._monitors: List[MonitorInfo] = detect_monitors()
        active_idx = self._cfg["ACTIVE_MONITOR"]
        print_monitor_info(self._monitors, active_idx)
        self._region: VirtualDesktop = get_mapping_region(self._monitors, active_idx)

        # Screen dimensions from mapping region
        self._screen_x = self._region.x
        self._screen_y = self._region.y
        self._screen_w = self._region.width
        self._screen_h = self._region.height

        # --- FIX 3: Double exponential smoothing state ---
        # Smoothed cursor position (pixels, absolute in virtual desktop)
        self._smooth_x: float = self._screen_x + self._screen_w / 2.0
        self._smooth_y: float = self._screen_y + self._screen_h / 2.0
        # Velocity components for double exponential
        self._vel_x: float = 0.0
        self._vel_y: float = 0.0
        # Previous smoothed values (for accurate delta tracking)
        self._prev_smooth_x: float = self._smooth_x
        self._prev_smooth_y: float = self._smooth_y
        # Previous output values
        self._prev_out_x: float = self._smooth_x
        self._prev_out_y: float = self._smooth_y

        # Click cooldown tracking
        self._last_click_time: float = 0.0
        self._last_right_click_time: float = 0.0
        self._last_double_click_time: float = 0.0

        # Drag state
        self._dragging: bool = False

        logger.info(
            "MouseController initialized — mapping region: %dx%d at (%d,%d), %d monitor(s)",
            self._screen_w, self._screen_h, self._screen_x, self._screen_y,
            len(self._monitors),
        )

    # ------------------------------------------------------------------
    #  Cursor movement
    # ------------------------------------------------------------------

    def move_to_normalized(self, nx: float, ny: float) -> None:
        """Move cursor using normalized coordinates (0.0–1.0).

        Applies double exponential smoothing, dead zone, and pointer
        acceleration. Coordinates are mapped to the active monitor region
        (or full virtual desktop if ACTIVE_MONITOR == -1).
        """
        # Clamp to [0, 1]
        nx = max(0.0, min(1.0, nx))
        ny = max(0.0, min(1.0, ny))

        # --- FIX 1: Map to monitor region (absolute virtual desktop coords) ---
        raw_x = self._screen_x + nx * self._screen_w
        raw_y = self._screen_y + ny * self._screen_h

        alpha = self._cfg["SMOOTHING_ALPHA"]
        beta = self._cfg["SMOOTHING_BETA"]
        dead_zone = self._cfg["DEAD_ZONE_PX"]
        accel_factor = self._cfg["ACCELERATION_FACTOR"]

        with self._lock:
            # --- FIX 3: Double exponential smoothing ---
            # Smoothed position
            new_smooth_x = alpha * raw_x + (1.0 - alpha) * (self._smooth_x + self._vel_x)
            new_smooth_y = alpha * raw_y + (1.0 - alpha) * (self._smooth_y + self._vel_y)

            # Velocity (trend)
            self._vel_x = beta * (new_smooth_x - self._smooth_x) + (1.0 - beta) * self._vel_x
            self._vel_y = beta * (new_smooth_y - self._smooth_y) + (1.0 - beta) * self._vel_y

            self._smooth_x = new_smooth_x
            self._smooth_y = new_smooth_y

            # --- FIX 3: Dead zone & stable acceleration ---
            # Calculate delta based on underlying smoothed coordinates, not output coordinates!
            # This prevents infinite feedback loops and rubber-banding.
            delta_x = self._smooth_x - self._prev_smooth_x
            delta_y = self._smooth_y - self._prev_smooth_y

            self._prev_smooth_x = self._smooth_x
            self._prev_smooth_y = self._smooth_y

            if abs(delta_x) < dead_zone and abs(delta_y) < dead_zone:
                return  # hand is basically still, don't jitter

            # --- FIX 3: Pointer acceleration ---
            velocity_mag = math.hypot(delta_x, delta_y)
            scale = 1.0 + (velocity_mag * accel_factor / 100.0)  # normalize so it's not extreme
            
            # Apply acceleration to the delta, then add to the PREVIOUS OUTPUT.
            # This converts absolute webcam mapping into a relative, mouse-like mapping.
            out_x = self._prev_out_x + delta_x * scale
            out_y = self._prev_out_y + delta_y * scale

            # Clamp to virtual desktop bounds
            out_x = max(self._screen_x, min(self._screen_x + self._screen_w - 1, out_x))
            out_y = max(self._screen_y, min(self._screen_y + self._screen_h - 1, out_y))

            self._prev_out_x = out_x
            self._prev_out_y = out_y

            target_x = int(out_x)
            target_y = int(out_y)

        try:
            pyautogui.moveTo(target_x, target_y, _pause=False)
        except Exception as exc:
            logger.error("moveTo failed: %s", exc)

    def move_relative(self, dx: int, dy: int) -> None:
        """Move cursor by a relative pixel offset."""
        try:
            pyautogui.moveRel(dx, dy, _pause=False)
        except Exception as exc:
            logger.error("moveRel failed: %s", exc)

    def get_position(self) -> Tuple[int, int]:
        """Return current smoothed cursor position in pixels."""
        with self._lock:
            return int(self._prev_out_x), int(self._prev_out_y)

    # ------------------------------------------------------------------
    #  Click actions
    # ------------------------------------------------------------------

    def _cooldown_ok(self, last_time: float) -> bool:
        """Check if enough time has passed since the last click."""
        cooldown = self._cfg["CLICK_COOLDOWN_MS"] / 1000.0
        return (time.time() - last_time) >= cooldown

    def click(self) -> None:
        """Perform a left click if cooldown has elapsed."""
        if not self._cooldown_ok(self._last_click_time):
            return
        self._last_click_time = time.time()
        try:
            pyautogui.click(_pause=False)
            logger.debug("Left click")
        except Exception as exc:
            logger.error("click failed: %s", exc)

    def double_click(self) -> None:
        """Perform a double click if cooldown has elapsed."""
        if not self._cooldown_ok(self._last_double_click_time):
            return
        self._last_double_click_time = time.time()
        try:
            pyautogui.doubleClick(_pause=False)
            logger.debug("Double click")
        except Exception as exc:
            logger.error("doubleClick failed: %s", exc)

    def right_click(self) -> None:
        """Perform a right click if cooldown has elapsed."""
        if not self._cooldown_ok(self._last_right_click_time):
            return
        self._last_right_click_time = time.time()
        try:
            pyautogui.rightClick(_pause=False)
            logger.debug("Right click")
        except Exception as exc:
            logger.error("rightClick failed: %s", exc)

    # ------------------------------------------------------------------
    #  Drag operations
    # ------------------------------------------------------------------

    def mouse_down(self) -> None:
        """Press and hold the left mouse button (start drag)."""
        with self._lock:
            if self._dragging:
                return
            self._dragging = True
        try:
            pyautogui.mouseDown(_pause=False)
            logger.debug("Mouse down (drag start)")
        except Exception as exc:
            logger.error("mouseDown failed: %s", exc)

    def mouse_up(self) -> None:
        """Release the left mouse button (end drag)."""
        with self._lock:
            if not self._dragging:
                return
            self._dragging = False
        try:
            pyautogui.mouseUp(_pause=False)
            logger.debug("Mouse up (drag end)")
        except Exception as exc:
            logger.error("mouseUp failed: %s", exc)

    @property
    def is_dragging(self) -> bool:
        """Whether the mouse is currently in drag mode."""
        with self._lock:
            return self._dragging

    # ------------------------------------------------------------------
    #  Scrolling
    # ------------------------------------------------------------------

    def scroll(self, amount: int) -> None:
        """Scroll by the given amount (positive = up, negative = down)."""
        try:
            pyautogui.scroll(amount, _pause=False)
        except Exception as exc:
            logger.error("scroll failed: %s", exc)

    # ------------------------------------------------------------------
    #  Keyboard shortcuts
    # ------------------------------------------------------------------

    def hotkey(self, *keys: str) -> None:
        """Press a keyboard shortcut (e.g., hotkey('alt', 'tab'))."""
        try:
            pyautogui.hotkey(*keys, _pause=False)
            logger.debug("Hotkey: %s", "+".join(keys))
        except Exception as exc:
            logger.error("hotkey %s failed: %s", "+".join(keys), exc)

    def press(self, key: str) -> None:
        """Press and release a single key."""
        try:
            pyautogui.press(key, _pause=False)
            logger.debug("Key press: %s", key)
        except Exception as exc:
            logger.error("press %s failed: %s", key, exc)

    # ------------------------------------------------------------------
    #  Window management (Win32)
    # ------------------------------------------------------------------

    def move_foreground_window(self, dx: int, dy: int) -> None:
        """Move the foreground window by (dx, dy) pixels using Win32 API.

        Used for palm-drag window movement.
        """
        try:
            import win32gui  # type: ignore
            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                rect = win32gui.GetWindowRect(hwnd)
                x, y, r, b = rect
                w = r - x
                h = b - y
                win32gui.MoveWindow(hwnd, x + dx, y + dy, w, h, True)
                logger.debug("Window moved by (%d, %d)", dx, dy)
        except ImportError:
            logger.warning("pywin32 not available — window move not supported")
        except Exception as exc:
            logger.error("move_foreground_window failed: %s", exc)

    def resize_foreground_window(self, dw: int, dh: int) -> None:
        """Resize the foreground window by (dw, dh) pixels using Win32 API.

        Used for two-hand expand/compress gestures.
        """
        try:
            import win32gui  # type: ignore
            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                rect = win32gui.GetWindowRect(hwnd)
                x, y, r, b = rect
                w = r - x
                h = b - y
                new_w = max(200, w + dw)
                new_h = max(200, h + dh)
                win32gui.MoveWindow(hwnd, x, y, new_w, new_h, True)
                logger.debug("Window resized by (%d, %d)", dw, dh)
        except ImportError:
            logger.warning("pywin32 not available — window resize not supported")
        except Exception as exc:
            logger.error("resize_foreground_window failed: %s", exc)
