"""
spatial_ui.py — Spatial window management for VISOR.

Translates high-level ``IntentResult`` objects into concrete OS actions
(window grab, move, resize, snap, throw) using the ``OSController``.

Features:
    * **Grab & move** — grab the foreground window and drag it with the hand.
    * **Edge-snap** — when a window is released near a screen edge it snaps
      to a half-screen or maximised layout (à la Windows Aero Snap).
    * **Throw** — releasing a window with high velocity flings it to the
      nearest snap zone.
    * **Voice / keyboard dispatch** — forwards hotkey and press commands.
    * **App launch** — opens applications by name or URL.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple

from visor.action.os_controller import OSController
from visor.core.types import Intent, IntentResult

logger = logging.getLogger("VISOR.action.spatial_ui")


class SpatialUIManager:
    """Spatial window management: grab, resize, throw, snap.

    Example::

        os_ctl = OSController()
        ui = SpatialUIManager(os_ctl, screen_width=1920, screen_height=1080)
        ui.handle_intent(intent_result, cursor_x=960, cursor_y=540)
    """

    # ── Class Constants ─────────────────────────────────────────────────

    SNAP_ZONE_PERCENT: float = 3.0
    """Percentage of screen dimension that defines the edge snap zone."""

    THROW_MIN_VELOCITY: float = 150.0
    """Minimum average velocity (px/s) to trigger a throw-to-snap."""

    # ── Lifecycle ───────────────────────────────────────────────────────

    def __init__(
        self,
        os_controller: OSController,
        screen_width: int,
        screen_height: int,
    ) -> None:
        self._os = os_controller
        self._screen_w = screen_width
        self._screen_h = screen_height

        # Grab state
        self._grabbed_hwnd: Optional[int] = None
        self._grab_offset: Tuple[int, int] = (0, 0)
        self._grab_origin: Tuple[int, int] = (0, 0)

        # Velocity tracking for throw gesture
        self._velocity_history: Deque[Tuple[float, int, int]] = deque(maxlen=10)

    # ── Public Dispatch ─────────────────────────────────────────────────

    def handle_intent(
        self,
        intent: IntentResult,
        cursor_x: int,
        cursor_y: int,
    ) -> None:
        """Dispatch an ``IntentResult`` to the appropriate OS action.

        Args:
            intent: Resolved intent from the intent layer.
            cursor_x: Current screen-space cursor X.
            cursor_y: Current screen-space cursor Y.
        """
        ctx = intent.context
        action = intent.intent

        if action == Intent.CURSOR_MOVE:
            self._os.move_cursor(cursor_x, cursor_y)

        elif action == Intent.DRAG_START:
            self._grab_window(cursor_x, cursor_y)

        elif action == Intent.DRAG_MOVE:
            self._move_window(cursor_x, cursor_y)

        elif action == Intent.DRAG_END:
            self._release_window(cursor_x, cursor_y)

        elif action == Intent.SELECT:
            self._os.click()

        elif action == Intent.CLOSE:
            self._os.hotkey("alt", "F4")

        elif action == Intent.CONFIRM:
            self._os.press("enter")

        elif action == Intent.NAVIGATE_BACK:
            self._os.hotkey("alt", "left")

        elif action == Intent.NAVIGATE_FORWARD:
            self._os.hotkey("alt", "right")

        elif action == Intent.SCROLL:
            direction = ctx.get("direction", "down")
            amount = int(ctx.get("amount", 5))
            # pyautogui.scroll: positive = up, negative = down
            self._os.scroll(amount if direction == "up" else -amount)

        elif action == Intent.VOICE_COMMAND:
            self._dispatch_voice_command(ctx)

        elif action == Intent.APP_LAUNCH:
            app_name = ctx.get("app", "")
            url = ctx.get("url")
            if url:
                self._launch_app(url)
            elif app_name:
                self._launch_app(app_name)

        elif action == Intent.IDLE:
            pass  # nothing to do

        else:
            logger.debug("Unhandled intent: %s", action)

    # ── Window Grab / Move / Release ────────────────────────────────────

    def _grab_window(self, x: int, y: int) -> None:
        """Begin dragging the foreground window.

        Records the window handle, its initial position, and the offset
        between the cursor and the window's top-left corner.
        """
        hwnd = self._os.get_foreground_window()
        if hwnd is None:
            logger.warning("No foreground window to grab")
            return

        rect = self._os.get_window_rect(hwnd)
        if rect is None:
            logger.warning("Could not get rect for hwnd 0x%X", hwnd)
            return

        win_x, win_y, _, _ = rect
        self._grabbed_hwnd = hwnd
        self._grab_offset = (x - win_x, y - win_y)
        self._grab_origin = (x, y)
        self._velocity_history.clear()
        self._velocity_history.append((time.monotonic(), x, y))
        logger.debug("Grabbed window 0x%X at offset (%d, %d)",
                      hwnd, *self._grab_offset)

    def _move_window(self, x: int, y: int) -> None:
        """Move the grabbed window to follow the cursor."""
        if self._grabbed_hwnd is None:
            return

        new_x = x - self._grab_offset[0]
        new_y = y - self._grab_offset[1]

        rect = self._os.get_window_rect(self._grabbed_hwnd)
        if rect is None:
            return

        win_x, win_y, right, bottom = rect
        w = right - win_x
        h = bottom - win_y
        dx = new_x - win_x
        dy = new_y - win_y

        if dx != 0 or dy != 0:
            self._os.move_window(self._grabbed_hwnd, dx, dy)

        # Track velocity
        self._velocity_history.append((time.monotonic(), x, y))

    def _release_window(self, x: int, y: int) -> None:
        """Release the grabbed window — snap or throw if applicable."""
        if self._grabbed_hwnd is None:
            logger.debug("release_window: nothing grabbed")
            self._clear_grab_state()
            return

        self._velocity_history.append((time.monotonic(), x, y))

        # Check edge-snap zones first
        snap_zone = self._check_snap_zone(x, y)
        if snap_zone is not None:
            logger.info("Snapping window to zone: %s", snap_zone)
            self._snap_window(snap_zone)
            self._clear_grab_state()
            return

        # Check throw velocity
        vx, vy = self._calculate_throw_velocity()
        speed = (vx ** 2 + vy ** 2) ** 0.5
        if speed >= self.THROW_MIN_VELOCITY:
            throw_zone = self._throw_target(vx, vy)
            if throw_zone is not None:
                logger.info("Throwing window to zone: %s (v=%.0f px/s)",
                            throw_zone, speed)
                self._snap_window(throw_zone)

        self._clear_grab_state()

    # ── Snap Zones ──────────────────────────────────────────────────────

    def _check_snap_zone(self, x: int, y: int) -> Optional[str]:
        """Determine if the cursor is in a screen-edge snap zone.

        Args:
            x: Cursor X in pixels.
            y: Cursor Y in pixels.

        Returns:
            ``"left_half"``, ``"right_half"``, ``"maximize"``, or ``None``.
        """
        margin_x = int(self._screen_w * self.SNAP_ZONE_PERCENT / 100.0)
        margin_y = int(self._screen_h * self.SNAP_ZONE_PERCENT / 100.0)

        # Top edge → maximise
        if y <= margin_y:
            return "maximize"

        # Left edge → left half
        if x <= margin_x:
            return "left_half"

        # Right edge → right half
        if x >= self._screen_w - margin_x:
            return "right_half"

        return None

    def _snap_window(self, zone: str) -> None:
        """Snap the grabbed window to the given zone using Win hotkeys.

        Args:
            zone: One of ``"left_half"``, ``"right_half"``, ``"maximize"``.
        """
        hotkeys = {
            "left_half": ("win", "left"),
            "right_half": ("win", "right"),
            "maximize": ("win", "up"),
        }
        keys = hotkeys.get(zone)
        if keys is not None:
            self._os.hotkey(*keys)
        else:
            logger.warning("Unknown snap zone: %s", zone)

    # ── Throw ───────────────────────────────────────────────────────────

    def _calculate_throw_velocity(self) -> Tuple[float, float]:
        """Compute average cursor velocity from recent history.

        Returns:
            ``(vx, vy)`` in pixels per second.
        """
        if len(self._velocity_history) < 2:
            return (0.0, 0.0)

        total_vx = 0.0
        total_vy = 0.0
        count = 0

        items = list(self._velocity_history)
        for i in range(1, len(items)):
            t0, x0, y0 = items[i - 1]
            t1, x1, y1 = items[i]
            dt = t1 - t0
            if dt > 0:
                total_vx += (x1 - x0) / dt
                total_vy += (y1 - y0) / dt
                count += 1

        if count == 0:
            return (0.0, 0.0)

        return (total_vx / count, total_vy / count)

    @staticmethod
    def _throw_target(vx: float, vy: float) -> Optional[str]:
        """Pick a snap zone based on throw direction.

        Args:
            vx: Horizontal velocity (positive = right).
            vy: Vertical velocity (positive = down).

        Returns:
            A snap zone name, or ``None`` if the direction is ambiguous.
        """
        # Predominantly vertical-up → maximise
        if vy < 0 and abs(vy) > abs(vx):
            return "maximize"
        # Predominantly horizontal-left
        if vx < 0 and abs(vx) > abs(vy):
            return "left_half"
        # Predominantly horizontal-right
        if vx > 0 and abs(vx) > abs(vy):
            return "right_half"
        return None

    # ── Voice Command Dispatch ──────────────────────────────────────────

    def _dispatch_voice_command(self, ctx: Dict[str, Any]) -> None:
        """Execute a voice command described by its context dict.

        Expects ``ctx["action"]`` to be ``"hotkey"`` or ``"press"``.
        """
        action = ctx.get("action")

        if action == "hotkey":
            keys = ctx.get("keys", [])
            if keys:
                self._os.hotkey(*keys)
            else:
                logger.warning("Voice hotkey command with no keys: %s", ctx)

        elif action == "press":
            key = ctx.get("key", "")
            if key:
                self._os.press(key)
            else:
                logger.warning("Voice press command with no key: %s", ctx)

        else:
            logger.warning("Unknown voice action: %s", action)

    # ── App Launch ──────────────────────────────────────────────────────

    def _launch_app(self, app_name: str) -> None:
        """Attempt to launch an application by name or URL.

        Tries ``os.startfile`` first (Windows-native), then falls back to
        ``subprocess.Popen``.

        Args:
            app_name: Application name, path, or URL.
        """
        logger.info("Launching app: '%s'", app_name)

        # os.startfile handles URLs, registered file types, Start-menu names
        if hasattr(os, "startfile"):
            try:
                os.startfile(app_name)  # type: ignore[attr-defined]
                return
            except OSError:
                logger.debug(
                    "os.startfile('%s') failed, trying subprocess", app_name,
                    exc_info=True,
                )

        # Fallback: try running as a bare command
        try:
            subprocess.Popen(
                app_name,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            logger.error("Failed to launch '%s'", app_name, exc_info=True)

    # ── Internal Helpers ────────────────────────────────────────────────

    def _clear_grab_state(self) -> None:
        """Reset all grab-related state."""
        self._grabbed_hwnd = None
        self._grab_offset = (0, 0)
        self._grab_origin = (0, 0)
        self._velocity_history.clear()
