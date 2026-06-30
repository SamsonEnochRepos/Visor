"""
config.py — Configuration loader with hot-reload for VISOR.

Loads config.json at startup and polls for changes every 2 seconds.
Thread-safe singleton access via Config.get().
"""

import json
import os
import threading
import time
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("VISOR.config")

_CONFIG_FILENAME = "config.json"
_POLL_INTERVAL = 2.0  # seconds between file modification checks

# Default values — used if config.json is missing or a key is absent
_DEFAULTS: Dict[str, Any] = {
    # --- Camera ---
    "CAMERA_INDEX": 0,
    "FRAME_WIDTH": 640,
    "FRAME_HEIGHT": 480,
    "TARGET_FPS": 60,

    # --- MediaPipe ---
    "NUM_HANDS": 2,
    "DETECTION_CONFIDENCE": 0.6,
    "TRACKING_CONFIDENCE": 0.5,

    # --- One Euro Filter ---
    "FILTER_MIN_CUTOFF": 1.0,
    "FILTER_BETA": 0.007,
    "FILTER_D_CUTOFF": 1.0,

    # --- Gesture Classifier ---
    "MIN_GESTURE_CONFIDENCE": 0.55,
    "MIN_GESTURE_STABILITY": 0.45,
    "STABILITY_WINDOW_FRAMES": 12,

    # --- Spatial UI ---
    "SNAP_ZONE_PERCENT": 3.0,
    "THROW_MIN_VELOCITY": 150,

    # --- Monitor ---
    "ACTIVE_MONITOR": -1,  # -1 = all monitors as unified space

    # --- Voice ---
    "VOICE_ENABLED": True,
    "MIC_DEVICE_INDEX": -1,
    "VOSK_MODEL_PATH": "vosk-model-small-en-us-0.15",
    "VOICE_STATUS_INTERVAL_SEC": 10,

    # --- UI ---
    "GESTURE_ENABLED": True,
    "OVERLAY_ENABLED": True,
    "SCROLL_SPEED": 10,

    # --- App Launcher ---
    "CUSTOM_APP_PATHS": {},
}


class Config:
    """Thread-safe singleton configuration manager with hot-reload.

    Usage:
        cfg = Config.get()
        value = cfg["SMOOTHING_FACTOR"]
    """

    _instance: Optional["Config"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._data: Dict[str, Any] = dict(_DEFAULTS)
        self._data_lock = threading.Lock()
        self._config_path = self._resolve_config_path()
        self._last_mtime: float = 0.0
        self._watcher_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._load()

    @classmethod
    def get(cls) -> "Config":
        """Return the singleton Config instance, creating it if needed."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = Config()
        return cls._instance

    def _resolve_config_path(self) -> str:
        """Find config.json next to this script file."""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(script_dir, _CONFIG_FILENAME)

    def _load(self) -> None:
        """Load or reload config.json, merging with defaults."""
        try:
            if os.path.exists(self._config_path):
                mtime = os.path.getmtime(self._config_path)
                with open(self._config_path, "r", encoding="utf-8") as f:
                    user_data = json.load(f)
                with self._data_lock:
                    self._data = dict(_DEFAULTS)
                    self._data.update(user_data)
                self._last_mtime = mtime
                logger.info("Config loaded from %s", self._config_path)
            else:
                logger.warning(
                    "Config file not found at %s — using defaults",
                    self._config_path,
                )
                self._write_defaults()
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load config: %s — keeping current values", exc)

    def _write_defaults(self) -> None:
        """Write default config.json if none exists."""
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(_DEFAULTS, f, indent=4)
            logger.info("Default config written to %s", self._config_path)
        except OSError as exc:
            logger.error("Could not write default config: %s", exc)

    def __getitem__(self, key: str) -> Any:
        """Get a config value by key. Falls back to default if missing."""
        with self._data_lock:
            return self._data.get(key, _DEFAULTS.get(key))

    def __contains__(self, key: str) -> bool:
        with self._data_lock:
            return key in self._data

    def get_value(self, key: str, default: Any = None) -> Any:
        """Get a config value with an explicit fallback."""
        with self._data_lock:
            return self._data.get(key, default)

    def all(self) -> Dict[str, Any]:
        """Return a snapshot copy of all config values."""
        with self._data_lock:
            return dict(self._data)

    def start_watcher(self) -> None:
        """Start the background file-change polling thread."""
        if self._watcher_thread is not None and self._watcher_thread.is_alive():
            return
        self._stop_event.clear()
        self._watcher_thread = threading.Thread(
            target=self._watch_loop, daemon=True, name="ConfigWatcher"
        )
        self._watcher_thread.start()
        logger.info("Config watcher started (polling every %.1fs)", _POLL_INTERVAL)

    def stop_watcher(self) -> None:
        """Stop the background file-change polling thread."""
        self._stop_event.set()
        if self._watcher_thread is not None:
            self._watcher_thread.join(timeout=5.0)
            self._watcher_thread = None
        logger.info("Config watcher stopped")

    def _watch_loop(self) -> None:
        """Poll config.json modification time and reload on change."""
        while not self._stop_event.is_set():
            try:
                if os.path.exists(self._config_path):
                    mtime = os.path.getmtime(self._config_path)
                    if mtime > self._last_mtime:
                        logger.info("Config file changed — reloading")
                        self._load()
            except OSError as exc:
                logger.error("Config watcher error: %s", exc)
            self._stop_event.wait(timeout=_POLL_INTERVAL)
