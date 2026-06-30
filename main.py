"""
main.py — Entry point for VISOR spatial computing platform.

Starts the system tray icon, perception pipeline, voice engine, and
HUD overlay as parallel threads. All threads are controlled via
threading.Event flags toggled from the tray menu.

Architecture v2.0: Uses the new visor/ package with async pipeline,
landmark-based gesture classifier, intent engine, and spatial UI.
"""

import os
import sys

# Suppress C++ log noise from MediaPipe/Abseil/TensorFlow (e.g. Clearcut telemetry errors)
os.environ["GLOG_minloglevel"] = "3"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

import logging
import threading
from typing import Dict, Any

# Ensure stdout uses UTF-8 to prevent UnicodeEncodeError with box-drawing chars
if sys.stdout and sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image, ImageDraw
import pystray

from config import Config
from monitor import detect_monitors, get_mapping_region, print_monitor_info
from visor.core.pipeline import Pipeline
from visor.input.audio_provider import PyAudioProvider
from visor.ui.hud import HUD

# ---------------------------------------------------------------------------
#  Logging setup — file + console
# ---------------------------------------------------------------------------
_LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "touchless_os.log"
)

file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.WARNING)
console_handler.setFormatter(logging.Formatter("  %(message)s"))

logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])
logger = logging.getLogger("VISOR.main")


# ---------------------------------------------------------------------------
#  Shared state — thread-safe dict read by HUD, written by engines
# ---------------------------------------------------------------------------
shared_state: Dict[str, Any] = {
    "gesture_state": "none",
    "gesture_status": "Starting",
    "gesture_confidence": "0%",
    "gesture_stability": "0%",
    "intent": "idle",
    "voice_status": "Starting",
    "fps": "—",
    "last_voice": "",
}


# ---------------------------------------------------------------------------
#  Threading events
# ---------------------------------------------------------------------------
gesture_enabled = threading.Event()
voice_enabled = threading.Event()
overlay_visible = threading.Event()
stop_event = threading.Event()


def _create_tray_icon_image() -> Image.Image:
    """Generate a VISOR tray icon (cyan hand silhouette on dark circle)."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, size - 4, size - 4], fill=(10, 10, 10, 220))
    draw.polygon(
        [(16, 18), (32, 48), (48, 18), (42, 18), (32, 40), (22, 18)],
        fill=(0, 229, 255, 255),
    )
    draw.ellipse([28, 12, 36, 18], fill=(57, 255, 20, 255))
    return img


def _toggle_gestures(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    if gesture_enabled.is_set():
        gesture_enabled.clear()
        logger.info("Gestures disabled")
    else:
        gesture_enabled.set()
        logger.info("Gestures enabled")


def _toggle_voice(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    if voice_enabled.is_set():
        voice_enabled.clear()
        logger.info("Voice disabled")
    else:
        voice_enabled.set()
        logger.info("Voice enabled")


def _toggle_overlay(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    if overlay_visible.is_set():
        overlay_visible.clear()
        logger.info("Overlay hidden")
    else:
        overlay_visible.set()
        logger.info("Overlay shown")


def _open_settings(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config.json"
    )
    try:
        if sys.platform == "win32":
            os.startfile(config_path)
        else:
            import subprocess
            subprocess.Popen(["xdg-open", config_path])
        logger.info("Opened settings file")
    except Exception as exc:
        logger.error("Could not open settings: %s", exc)


def _quit(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    logger.info("Quit requested — shutting down")
    stop_event.set()
    icon.stop()


def _print_startup_checklist(cfg: Config) -> None:
    """Print a formatted startup checklist to console."""
    print()
    print("  ╔════════════════════════════════════════════╗")
    print("  ║    VISOR v2.0 — Spatial Computing Platform ║")
    print("  ╠════════════════════════════════════════════╣")

    # Camera
    cam_idx = cfg.get_value("CAMERA_INDEX", 0)
    import cv2
    cap = cv2.VideoCapture(cam_idx, cv2.CAP_DSHOW)
    if cap.isOpened():
        print(f"  ║  [OK] Camera found at index {cam_idx:<12}║")
        cap.release()
    else:
        cap = cv2.VideoCapture(cam_idx)
        if cap.isOpened():
            print(f"  ║  [OK] Camera found at index {cam_idx:<12}║")
            cap.release()
        else:
            print(f"  ║  [!!] Camera NOT found (index {cam_idx:<9})║")

    # Monitors
    monitors = detect_monitors()
    active_idx = cfg.get_value("ACTIVE_MONITOR", -1)
    region = get_mapping_region(monitors, active_idx)
    if active_idx < 0:
        print(f"  ║  [OK] {len(monitors)} monitor(s), unified desktop    ║")
    else:
        print(f"  ║  [OK] Monitor {active_idx} selected               ║")
    print_monitor_info(monitors, active_idx)

    # Hand model
    script_dir = os.path.dirname(os.path.abspath(__file__))
    model_path = os.path.join(script_dir, "hand_landmarker.task")
    if os.path.exists(model_path):
        print("  ║  [OK] Hand landmarker model loaded        ║")
    else:
        print("  ║  [!!] Hand model NOT found                ║")

    # Vosk model
    vosk_path = os.path.join(
        script_dir, cfg.get_value("VOSK_MODEL_PATH", "vosk-model-small-en-us-0.15")
    )
    if os.path.isdir(vosk_path):
        print("  ║  [OK] Vosk model loaded                   ║")
    else:
        print("  ║  [!!] Vosk model NOT found                ║")

    # Architecture
    print("  ║  [OK] Architecture: v2.0 (Intent Layer)   ║")
    print(f"  ║  [OK] Gestures: {'ON' if cfg['GESTURE_ENABLED'] else 'OFF'}                        ║")
    print(f"  ║  [OK] Voice: {'ON' if cfg['VOICE_ENABLED'] else 'OFF'}                           ║")
    print("  ║                                            ║")
    print("  ║  [OK] All systems ready                    ║")
    print("  ╚════════════════════════════════════════════╝")
    print()


def _run_voice_engine(shared_state: Dict[str, Any],
                      enabled_event: threading.Event,
                      stop_event: threading.Event) -> None:
    """Run the voice engine in its own thread using the new intent layer."""
    import json
    import time
    import webbrowser
    import subprocess

    cfg = Config.get()
    audio = PyAudioProvider(
        sample_rate=16000,
        chunk_size=8192,
        device_index=cfg.get_value("MIC_DEVICE_INDEX", -1),
    )

    # Enumerate devices
    PyAudioProvider.enumerate_devices()

    # Load Vosk
    try:
        import vosk
    except ImportError:
        logger.error("Vosk not installed — voice disabled")
        shared_state["voice_status"] = "Missing: vosk"
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    model_path = os.path.join(
        script_dir, cfg.get_value("VOSK_MODEL_PATH", "vosk-model-small-en-us-0.15")
    )
    if not os.path.isdir(model_path):
        logger.error("Vosk model not found at %s", model_path)
        shared_state["voice_status"] = "Model not found"
        return

    try:
        vosk.SetLogLevel(-1)
        model = vosk.Model(model_path)
        logger.info("Vosk model loaded: %s", model_path)
    except Exception as exc:
        logger.error("Vosk model load failed: %s", exc)
        shared_state["voice_status"] = "Model error"
        return

    if not audio.start():
        shared_state["voice_status"] = "Mic not found"
        return

    recognizer = vosk.KaldiRecognizer(model, audio.get_sample_rate())
    shared_state["voice_status"] = "Listening"
    logger.info("Voice engine started — listening")

    # Import intent resolver
    from visor.intent.voice_intent import VoiceIntentResolver
    from visor.action.os_controller import OSController
    from visor.action.spatial_ui import SpatialUIManager

    voice_resolver = VoiceIntentResolver()
    os_ctrl = OSController()

    try:
        while not stop_event.is_set():
            if not enabled_event.is_set():
                shared_state["voice_status"] = "Paused"
                stop_event.wait(timeout=0.1)
                continue

            data = audio.read_chunk()
            if data is None:
                continue

            try:
                if recognizer.AcceptWaveform(data):
                    result = json.loads(recognizer.Result())
                    text = result.get("text", "").strip().lower()
                    if text:
                        logger.info("Voice recognized: '%s'", text)
                        shared_state["last_voice"] = text

                        # Resolve through intent layer
                        intent_result = voice_resolver.resolve(text)
                        if intent_result is not None:
                            _execute_voice_intent(
                                intent_result, os_ctrl, cfg
                            )
            except Exception as exc:
                logger.error("Voice recognition error: %s", exc)

            shared_state["voice_status"] = "Listening"

    except Exception as exc:
        logger.error("Voice engine crashed: %s", exc)
        shared_state["voice_status"] = f"Error: {exc}"
    finally:
        audio.stop()
        logger.info("Voice engine stopped")


def _execute_voice_intent(intent_result, os_ctrl, cfg) -> None:
    """Execute a voice intent through the OS controller."""
    from visor.core.types import Intent
    import webbrowser
    import subprocess

    intent = intent_result.intent
    ctx = intent_result.context

    if intent == Intent.VOICE_COMMAND:
        action = ctx.get("action", "")
        if action == "press":
            os_ctrl.press(ctx.get("key", ""))
        elif action == "hotkey":
            keys = ctx.get("keys", [])
            if keys:
                os_ctrl.hotkey(*keys)
        elif action == "scroll":
            os_ctrl.scroll(ctx.get("amount", 5))

    elif intent == Intent.APP_LAUNCH:
        app = ctx.get("app", "")
        url = ctx.get("url", "")
        if url:
            try:
                webbrowser.open(url)
                logger.info("Opened URL: %s", url)
            except Exception as exc:
                logger.error("URL open failed: %s", exc)
        elif app:
            _open_app(app, cfg)

    elif intent == Intent.CLOSE:
        os_ctrl.hotkey("alt", "F4")

    elif intent == Intent.SCROLL:
        direction = ctx.get("direction", "up")
        amount = ctx.get("amount", 10)
        os_ctrl.scroll(amount if direction == "up" else -amount)

    elif intent == Intent.CONFIRM:
        os_ctrl.press("enter")


def _open_app(app_name: str, cfg: Config) -> None:
    """Launch an application by name."""
    import subprocess

    custom_paths = cfg.get_value("CUSTOM_APP_PATHS", {})
    if app_name in custom_paths:
        try:
            subprocess.Popen(custom_paths[app_name], shell=True)
            logger.info("Opened custom app: %s", app_name)
            return
        except Exception as exc:
            logger.error("Custom app launch failed: %s", exc)

    if sys.platform == "win32":
        try:
            os.startfile(app_name)
            logger.info("Opened app: %s", app_name)
            return
        except OSError:
            pass

    try:
        subprocess.Popen(app_name, shell=True)
        logger.info("Opened app via subprocess: %s", app_name)
    except Exception as exc:
        logger.error("Could not open '%s': %s", app_name, exc)


def main() -> None:
    """Initialize and start all VISOR subsystems."""
    logger.info("=" * 60)
    logger.info("VISOR v2.0 starting — Spatial Computing Platform")
    logger.info("=" * 60)

    print()
    print("  ════════════════════════════════════════════")
    print("   VISOR v2.0 — Spatial Computing Platform")
    print("  ════════════════════════════════════════════")

    # Load config
    cfg = Config.get()
    cfg.start_watcher()

    # Print startup checklist
    _print_startup_checklist(cfg)

    # Set initial toggle states from config
    if cfg["GESTURE_ENABLED"]:
        gesture_enabled.set()
    if cfg["VOICE_ENABLED"]:
        voice_enabled.set()
    if cfg.get_value("OVERLAY_ENABLED", True):
        overlay_visible.set()

    # --- Create the new pipeline ---
    pipeline = Pipeline(shared_state, gesture_enabled, stop_event)

    # --- Create HUD ---
    hud = HUD(shared_state, overlay_visible, stop_event)

    # Start threads
    threads = []

    pipeline_thread = threading.Thread(
        target=pipeline.run, daemon=True, name="Pipeline"
    )
    pipeline_thread.start()
    threads.append(pipeline_thread)
    logger.info("Pipeline thread started")

    voice_thread = threading.Thread(
        target=_run_voice_engine,
        args=(shared_state, voice_enabled, stop_event),
        daemon=True,
        name="VoiceEngine",
    )
    voice_thread.start()
    threads.append(voice_thread)
    logger.info("Voice thread started")

    # Build tray menu
    menu = pystray.Menu(
        pystray.MenuItem(
            "Gestures",
            _toggle_gestures,
            checked=lambda item: gesture_enabled.is_set(),
        ),
        pystray.MenuItem(
            "Voice",
            _toggle_voice,
            checked=lambda item: voice_enabled.is_set(),
        ),
        pystray.MenuItem(
            "Overlay",
            _toggle_overlay,
            checked=lambda item: overlay_visible.is_set(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Settings", _open_settings),
        pystray.MenuItem("Quit", _quit),
    )

    icon = pystray.Icon("VISOR", _create_tray_icon_image(), "VISOR v2.0", menu)
    logger.info("System tray icon ready")

    # Run pystray in background (Tkinter must be on main thread)
    icon_thread = threading.Thread(
        target=icon.run, daemon=True, name="TrayIcon"
    )
    icon_thread.start()
    threads.append(icon_thread)

    # Run HUD on main thread (Tkinter requirement)
    try:
        hud.run()
    except Exception as exc:
        logger.error("HUD error: %s", exc)
    finally:
        stop_event.set()
        cfg.stop_watcher()
        icon.stop()
        for t in threads:
            if t != threading.current_thread():
                t.join(timeout=3.0)
        logger.info("VISOR v2.0 shut down cleanly")


if __name__ == "__main__":
    main()
