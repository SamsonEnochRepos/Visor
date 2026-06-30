"""
context_manager.py — OS context provider for the VISOR intent layer.

Queries the operating system for information about the UI element (window)
under the cursor.  On Windows this uses ``win32gui``; on other platforms
the provider degrades gracefully and returns an empty context dict.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("VISOR.intent.context")

# Try importing win32gui at module load so the rest of the module can
# check the flag without repeated try/except blocks.
_HAS_WIN32: bool = False
try:
    import win32gui  # type: ignore[import-untyped]
    _HAS_WIN32 = True
except ImportError:
    pass


class ContextManager:
    """Provides OS-level context about the element under the cursor.

    Currently Windows-only (requires ``pywin32``).  On unsupported
    platforms every query silently returns an empty dict so the rest of
    the pipeline can continue without crashing.

    Example::

        ctx = ContextManager()
        info = ctx.get_context((800, 450))
        # {'window_hwnd': 12345, 'window_title': 'Notepad', 'window_class': 'Notepad'}
    """

    # ── Public API ──────────────────────────────────────────────────────

    def get_context(self, cursor_pixel_pos: Tuple[int, int]) -> Dict[str, Any]:
        """Return a context dict for the window under *cursor_pixel_pos*.

        Args:
            cursor_pixel_pos: Screen-space pixel position ``(x, y)``.

        Returns:
            A dict with ``window_hwnd``, ``window_title``, and
            ``window_class`` keys.  Empty dict if the platform is not
            supported or an error occurs.
        """
        if not _HAS_WIN32:
            return {}

        x, y = cursor_pixel_pos
        try:
            hwnd = self._get_window_at_point(x, y)
            if hwnd is None or hwnd == 0:
                return {}

            title: str = win32gui.GetWindowText(hwnd)
            cls: str = win32gui.GetClassName(hwnd)

            return {
                "window_hwnd": hwnd,
                "window_title": title,
                "window_class": cls,
            }
        except Exception:
            logger.warning(
                "Failed to query window context at (%d, %d)", x, y,
                exc_info=True,
            )
            return {}

    # ── Private Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _get_window_at_point(x: int, y: int) -> Optional[int]:
        """Return the window handle at screen coordinate ``(x, y)``.

        Uses ``win32gui.WindowFromPoint`` which maps directly to the
        Win32 ``WindowFromPoint`` API.

        Args:
            x: Horizontal screen pixel.
            y: Vertical screen pixel.

        Returns:
            A window handle (``HWND``) or ``None`` on failure.
        """
        try:
            hwnd: int = win32gui.WindowFromPoint((x, y))
            return hwnd if hwnd else None
        except Exception:
            logger.debug(
                "WindowFromPoint failed at (%d, %d)", x, y, exc_info=True,
            )
            return None
