"""
os_controller.py — Thread-safe OS I/O wrapper for VISOR.

A slim facade over ``pyautogui`` (cursor, clicks, keyboard, scroll) and
``win32gui`` (window management).  Contains *no* gesture logic, smoothing,
or intent mapping — those belong in the layers above.

All mutating operations acquire a shared lock so the controller is safe
to call from both the gesture thread and the voice thread.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional, Tuple

logger = logging.getLogger("VISOR.action.os_controller")

# ── Optional Win32 import ───────────────────────────────────────────────
_HAS_WIN32: bool = False
try:
    import win32gui  # type: ignore[import-untyped]
    _HAS_WIN32 = True
except ImportError:
    logger.info("win32gui not available — window management disabled")

# ── pyautogui import ────────────────────────────────────────────────────
try:
    import pyautogui
except ImportError as exc:
    raise ImportError(
        "pyautogui is required by OSController.  "
        "Install it with:  pip install pyautogui"
    ) from exc


class OSController:
    """Thin, thread-safe wrapper around OS input/output primitives.

    Example::

        ctl = OSController()
        ctl.move_cursor(960, 540)
        ctl.click()
    """

    # ── Lifecycle ───────────────────────────────────────────────────────

    def __init__(self) -> None:
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0.0

        self._lock = threading.Lock()
        self._dragging: bool = False

    # ── Cursor ──────────────────────────────────────────────────────────

    def move_cursor(self, x: int, y: int) -> None:
        """Move the mouse cursor to absolute screen position ``(x, y)``.

        Args:
            x: Horizontal pixel coordinate.
            y: Vertical pixel coordinate.
        """
        try:
            pyautogui.moveTo(x, y, _pause=False)
        except Exception:
            logger.error("move_cursor(%d, %d) failed", x, y, exc_info=True)

    def move_relative(self, dx: int, dy: int) -> None:
        """Move the cursor by a relative offset.

        Args:
            dx: Horizontal pixel delta.
            dy: Vertical pixel delta.
        """
        try:
            pyautogui.moveRel(dx, dy, _pause=False)
        except Exception:
            logger.error("move_relative(%d, %d) failed", dx, dy, exc_info=True)

    def get_position(self) -> Tuple[int, int]:
        """Return the current ``(x, y)`` screen position of the cursor."""
        try:
            pos = pyautogui.position()
            return (pos.x, pos.y)
        except Exception:
            logger.error("get_position() failed", exc_info=True)
            return (0, 0)

    # ── Clicks ──────────────────────────────────────────────────────────

    def click(self) -> None:
        """Perform a left click at the current cursor position."""
        with self._lock:
            try:
                pyautogui.click(_pause=False)
                logger.debug("click()")
            except Exception:
                logger.error("click() failed", exc_info=True)

    def double_click(self) -> None:
        """Perform a double left click at the current cursor position."""
        with self._lock:
            try:
                pyautogui.doubleClick(_pause=False)
                logger.debug("double_click()")
            except Exception:
                logger.error("double_click() failed", exc_info=True)

    def right_click(self) -> None:
        """Perform a right click at the current cursor position."""
        with self._lock:
            try:
                pyautogui.rightClick(_pause=False)
                logger.debug("right_click()")
            except Exception:
                logger.error("right_click() failed", exc_info=True)

    # ── Drag ────────────────────────────────────────────────────────────

    def mouse_down(self) -> None:
        """Press and hold the left mouse button."""
        with self._lock:
            try:
                pyautogui.mouseDown(_pause=False)
                self._dragging = True
                logger.debug("mouse_down()")
            except Exception:
                logger.error("mouse_down() failed", exc_info=True)

    def mouse_up(self) -> None:
        """Release the left mouse button."""
        with self._lock:
            try:
                pyautogui.mouseUp(_pause=False)
                self._dragging = False
                logger.debug("mouse_up()")
            except Exception:
                logger.error("mouse_up() failed", exc_info=True)

    @property
    def is_dragging(self) -> bool:
        """Whether the left mouse button is currently held down."""
        return self._dragging

    # ── Keyboard ────────────────────────────────────────────────────────

    def hotkey(self, *keys: str) -> None:
        """Press a keyboard hotkey combination (e.g. ``hotkey('ctrl', 'c')``).

        Args:
            *keys: Key names recognised by ``pyautogui``.
        """
        with self._lock:
            try:
                pyautogui.hotkey(*keys, _pause=False)
                logger.debug("hotkey(%s)", ", ".join(keys))
            except Exception:
                logger.error("hotkey(%s) failed", ", ".join(keys), exc_info=True)

    def press(self, key: str) -> None:
        """Press and release a single key.

        Args:
            key: Key name recognised by ``pyautogui``.
        """
        with self._lock:
            try:
                pyautogui.press(key, _pause=False)
                logger.debug("press(%s)", key)
            except Exception:
                logger.error("press(%s) failed", key, exc_info=True)

    # ── Scroll ──────────────────────────────────────────────────────────

    def scroll(self, amount: int) -> None:
        """Scroll the mouse wheel.

        Args:
            amount: Positive scrolls *up*, negative scrolls *down*.
        """
        with self._lock:
            try:
                pyautogui.scroll(amount, _pause=False)
                logger.debug("scroll(%d)", amount)
            except Exception:
                logger.error("scroll(%d) failed", amount, exc_info=True)

    # ── Window Management (Win32) ───────────────────────────────────────

    def move_window(self, hwnd: int, dx: int, dy: int) -> None:
        """Move a window by ``(dx, dy)`` pixels relative to its current position.

        Args:
            hwnd: Win32 window handle.
            dx: Horizontal pixel delta.
            dy: Vertical pixel delta.
        """
        if not _HAS_WIN32:
            logger.warning("move_window: win32gui not available")
            return
        try:
            rect = win32gui.GetWindowRect(hwnd)
            x, y, right, bottom = rect
            w = right - x
            h = bottom - y
            win32gui.MoveWindow(hwnd, x + dx, y + dy, w, h, True)
        except Exception:
            logger.error("move_window(0x%X, %d, %d) failed", hwnd, dx, dy,
                         exc_info=True)

    def resize_window(self, hwnd: int, dw: int, dh: int) -> None:
        """Resize a window by ``(dw, dh)`` pixels.

        Args:
            hwnd: Win32 window handle.
            dw: Width change in pixels.
            dh: Height change in pixels.
        """
        if not _HAS_WIN32:
            logger.warning("resize_window: win32gui not available")
            return
        try:
            rect = win32gui.GetWindowRect(hwnd)
            x, y, right, bottom = rect
            w = right - x
            h = bottom - y
            win32gui.MoveWindow(hwnd, x, y, w + dw, h + dh, True)
        except Exception:
            logger.error("resize_window(0x%X, %d, %d) failed", hwnd, dw, dh,
                         exc_info=True)

    def get_foreground_window(self) -> Optional[int]:
        """Return the handle of the current foreground window, or ``None``."""
        if not _HAS_WIN32:
            return None
        try:
            hwnd: int = win32gui.GetForegroundWindow()
            return hwnd if hwnd else None
        except Exception:
            logger.error("get_foreground_window() failed", exc_info=True)
            return None

    def get_window_rect(self, hwnd: int) -> Optional[Tuple[int, int, int, int]]:
        """Return ``(left, top, right, bottom)`` of a window, or ``None``.

        Args:
            hwnd: Win32 window handle.
        """
        if not _HAS_WIN32:
            return None
        try:
            rect = win32gui.GetWindowRect(hwnd)
            return (rect[0], rect[1], rect[2], rect[3])
        except Exception:
            logger.error("get_window_rect(0x%X) failed", hwnd, exc_info=True)
            return None
