"""
monitor.py — Multi-monitor detection and virtual desktop mapping for VISOR.

Detects all connected monitors at startup using screeninfo (with Win32/tkinter
fallback). Provides a unified virtual desktop bounding box so hand landmarks
map seamlessly across extended displays.
"""

import logging
from typing import List, NamedTuple, Optional

from config import Config

logger = logging.getLogger("VISOR.monitor")


class MonitorInfo(NamedTuple):
    """Rectangle describing one monitor's position in virtual desktop space."""
    x: int
    y: int
    width: int
    height: int
    name: str


class VirtualDesktop(NamedTuple):
    """Bounding box of the entire virtual desktop (all monitors combined)."""
    x: int
    y: int
    width: int
    height: int


def detect_monitors() -> List[MonitorInfo]:
    """Detect all connected monitors. Tries screeninfo first, then Win32, then tkinter."""
    monitors: List[MonitorInfo] = []

    # --- Strategy 1: screeninfo ---
    try:
        from screeninfo import get_monitors
        for m in get_monitors():
            monitors.append(MonitorInfo(
                x=m.x, y=m.y, width=m.width, height=m.height,
                name=m.name or f"Monitor@{m.x},{m.y}"
            ))
        if monitors:
            return monitors
    except ImportError:
        logger.debug("screeninfo not installed, trying Win32 fallback")
    except Exception as exc:
        logger.warning("screeninfo failed: %s, trying fallback", exc)

    # --- Strategy 2: Win32 EnumDisplayMonitors ---
    try:
        import ctypes
        import ctypes.wintypes

        monitors_w32: List[MonitorInfo] = []

        def _enum_callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
            mi = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetMonitorInfoW  # ensure available
            # Use MONITORINFO struct
            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.wintypes.DWORD),
                    ("rcMonitor", ctypes.wintypes.RECT),
                    ("rcWork", ctypes.wintypes.RECT),
                    ("dwFlags", ctypes.wintypes.DWORD),
                ]
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            ctypes.windll.user32.GetMonitorInfoW(hMonitor, ctypes.byref(info))
            rc = info.rcMonitor
            monitors_w32.append(MonitorInfo(
                x=rc.left, y=rc.top,
                width=rc.right - rc.left, height=rc.bottom - rc.top,
                name=f"Monitor@{rc.left},{rc.top}"
            ))
            return True

        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.wintypes.HMONITOR,
            ctypes.wintypes.HDC,
            ctypes.POINTER(ctypes.wintypes.RECT),
            ctypes.wintypes.LPARAM,
        )
        ctypes.windll.user32.EnumDisplayMonitors(
            None, None, MONITORENUMPROC(_enum_callback), 0
        )
        if monitors_w32:
            return monitors_w32
    except Exception as exc:
        logger.warning("Win32 monitor detection failed: %s, trying tkinter", exc)

    # --- Strategy 3: tkinter (only gets primary) ---
    try:
        import tkinter as _tk
        _root = _tk.Tk()
        _root.withdraw()
        w = _root.winfo_screenwidth()
        h = _root.winfo_screenheight()
        _root.destroy()
        monitors.append(MonitorInfo(x=0, y=0, width=w, height=h, name="Primary (tkinter)"))
        return monitors
    except Exception as exc:
        logger.error("All monitor detection methods failed: %s", exc)

    # Absolute fallback
    monitors.append(MonitorInfo(x=0, y=0, width=1920, height=1080, name="Fallback 1920x1080"))
    return monitors


def get_virtual_desktop(monitors: List[MonitorInfo]) -> VirtualDesktop:
    """Calculate the bounding box enclosing ALL monitors (unified virtual desktop)."""
    min_x = min(m.x for m in monitors)
    min_y = min(m.y for m in monitors)
    max_x = max(m.x + m.width for m in monitors)
    max_y = max(m.y + m.height for m in monitors)
    return VirtualDesktop(x=min_x, y=min_y, width=max_x - min_x, height=max_y - min_y)


def get_mapping_region(monitors: List[MonitorInfo], active_index: int) -> VirtualDesktop:
    """Return the coordinate region to map hand landmarks onto.

    Args:
        monitors: List of detected monitors.
        active_index: -1 = all monitors as unified desktop,
                       0+ = single monitor by index.

    Returns:
        VirtualDesktop bounding box for the target region.
    """
    if active_index < 0 or active_index >= len(monitors):
        # Unified: entire virtual desktop
        return get_virtual_desktop(monitors)
    else:
        m = monitors[active_index]
        return VirtualDesktop(x=m.x, y=m.y, width=m.width, height=m.height)


def print_monitor_info(monitors: List[MonitorInfo], active_index: int) -> None:
    """Print detected monitors to console for user verification."""
    print("\n  ╔══════════════════════════════════════╗")
    print("  ║       DETECTED MONITORS              ║")
    print("  ╠══════════════════════════════════════╣")
    for i, m in enumerate(monitors):
        marker = " ◄ active" if (active_index >= 0 and i == active_index) else ""
        print(f"  ║  [{i}] {m.name:<20s}              ║")
        print(f"  ║      pos=({m.x},{m.y})  {m.width}x{m.height}{marker}")
    vdesk = get_virtual_desktop(monitors)
    if active_index < 0:
        print(f"  ║                                      ║")
        print(f"  ║  UNIFIED DESKTOP: {vdesk.width}x{vdesk.height}  ◄ active")
        print(f"  ║  origin=({vdesk.x},{vdesk.y})")
    print("  ╚══════════════════════════════════════╝\n")
    logger.info("Detected %d monitor(s), virtual desktop: %dx%d at (%d,%d)",
                len(monitors), vdesk.width, vdesk.height, vdesk.x, vdesk.y)
