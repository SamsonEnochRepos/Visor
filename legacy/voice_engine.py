"""
voice_engine.py - Offline voice command recognition using Vosk.

Runs in its own thread, continuously listening on the default microphone.
Parses recognized speech against a command dictionary and dispatches actions.

FIX 5: Device enumeration, configurable mic index, model path from config,
       graceful fallback, larger buffer, periodic status indicator.
"""

import os
import sys
import json
import time
import logging
import threading
import subprocess
import webbrowser
from typing import Dict, Any, Optional

import pyautogui

from config import Config

logger = logging.getLogger("VISOR.voice")


def enumerate_audio_devices() -> None:
    """FIX 5: Print all audio input devices at startup."""
    try:
        import pyaudio
        p = pyaudio.PyAudio()
        print("\n  ╔══════════════════════════════════════╗")
        print("  ║       AUDIO INPUT DEVICES            ║")
        print("  ╠══════════════════════════════════════╣")
        found = 0
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d['maxInputChannels'] > 0:
                print(f"  ║  [{i}] {d['name'][:30]:<30s}  ║")
                found += 1
        if found == 0:
            print("  ║  No input devices found!            ║")
        print("  ╚══════════════════════════════════════╝\n")
        p.terminate()
        return
    except ImportError:
        print("  [WARN] PyAudio not installed — cannot enumerate devices")
    except Exception as exc:
        print(f"  [WARN] Audio enumeration failed: {exc}")


class VoiceEngine:
    """Offline speech-to-text engine using Vosk with command parsing."""

    def __init__(self, shared_state: Dict[str, Any],
                 enabled_event: threading.Event,
                 stop_event: threading.Event) -> None:
        self._shared = shared_state
        self._enabled = enabled_event
        self._stop = stop_event
        self._cfg = Config.get()
        self._model = None
        self._recognizer = None

    def run(self) -> None:
        """Main loop — call this from a thread."""
        logger.info("Voice engine starting")

        # FIX 5: Enumerate devices first
        enumerate_audio_devices()

        # Import vosk and pyaudio here so failures are caught gracefully
        try:
            import vosk
            import pyaudio
        except ImportError as exc:
            logger.error("Missing dependency for voice: %s", exc)
            self._shared["voice_status"] = f"Missing: {exc.name}"
            print(f"  [FAIL] Voice dependency missing: {exc.name}")
            return

        # FIX 5: Locate model from config
        script_dir = os.path.dirname(os.path.abspath(__file__))
        model_rel = self._cfg["VOSK_MODEL_PATH"]
        model_path = os.path.join(script_dir, model_rel)

        # FIX 5: Graceful model-not-found handling
        if not os.path.isdir(model_path):
            logger.error("Vosk model not found at %s", model_path)
            self._shared["voice_status"] = "Model not found"
            print(f"  [FAIL] Vosk model not found at: {model_path}")
            print("         Download from: https://alphacephei.com/vosk/models")
            print("         Use: vosk-model-small-en-us-0.15")
            print("         Voice disabled — system continues without voice.")
            return

        try:
            vosk.SetLogLevel(-1)  # Suppress vosk's own logging
            self._model = vosk.Model(model_path)
            logger.info("Vosk model loaded from %s", model_path)
            print(f"  [OK] Vosk model loaded: {model_rel}")
        except Exception as exc:
            logger.error("Failed to load Vosk model: %s", exc)
            self._shared["voice_status"] = "Model load error"
            print(f"  [FAIL] Vosk model load error: {exc}")
            return

        # FIX 5: Open microphone with configurable device and larger buffer
        sample_rate = 16000
        chunk_size = 8192  # FIX 5: increased from 4000

        mic_index = self._cfg["MIC_DEVICE_INDEX"]
        mic_device = None if mic_index < 0 else mic_index

        try:
            pa = pyaudio.PyAudio()

            # Print which device we're using
            if mic_device is not None:
                dev_info = pa.get_device_info_by_index(mic_device)
                print(f"  [OK] Microphone: {dev_info['name']} [index {mic_device}]")
            else:
                default_info = pa.get_default_input_device_info()
                print(f"  [OK] Microphone: {default_info['name']} [system default]")

            stream_kwargs = dict(
                format=pyaudio.paInt16,
                channels=1,
                rate=sample_rate,
                input=True,
                frames_per_buffer=chunk_size,
            )
            if mic_device is not None:
                stream_kwargs["input_device_index"] = mic_device

            stream = pa.open(**stream_kwargs)
        except Exception as exc:
            logger.error("Microphone init failed: %s", exc)
            self._shared["voice_status"] = "Mic not found"
            print(f"  [FAIL] Microphone init failed: {exc}")
            return

        self._recognizer = vosk.KaldiRecognizer(self._model, sample_rate)
        self._shared["voice_status"] = "Listening"
        logger.info("Voice engine started — listening")
        print("  [OK] Voice engine: Listening")

        last_status_print = time.time()
        status_interval = self._cfg.get_value("VOICE_STATUS_INTERVAL_SEC", 10)

        try:
            while not self._stop.is_set():
                if not self._enabled.is_set():
                    self._shared["voice_status"] = "Paused"
                    self._stop.wait(timeout=0.1)
                    continue

                # FIX 5: Periodic status indicator
                now = time.time()
                if now - last_status_print >= status_interval:
                    status = self._shared.get("voice_status", "unknown")
                    print(f"  Voice: {status}...")
                    last_status_print = now

                try:
                    data = stream.read(chunk_size, exception_on_overflow=False)
                except Exception as exc:
                    logger.error("Mic read error: %s", exc)
                    continue

                # FIX 5: Wrap recognition in try/except
                try:
                    if self._recognizer.AcceptWaveform(data):
                        result = json.loads(self._recognizer.Result())
                        text = result.get("text", "").strip().lower()
                        if text:
                            logger.info("Voice recognized: '%s'", text)
                            self._shared["last_voice"] = text
                            self._dispatch_command(text)
                except Exception as exc:
                    logger.error("Vosk recognition error: %s", exc)

                self._shared["voice_status"] = "Listening"

        except Exception as exc:
            logger.error("Voice engine crashed: %s", exc)
            self._shared["voice_status"] = f"Error: {exc}"
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()
            logger.info("Voice engine stopped")

    def _dispatch_command(self, text: str) -> None:
        """Parse recognized text and execute the matching command."""
        cfg = self._cfg

        # --- App launching ---
        if text.startswith("open "):
            app_name = text[5:].strip()
            self._open_app(app_name)
            return

        # --- Search ---
        if text.startswith("search "):
            query = text[7:].strip()
            if query:
                url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
                try:
                    webbrowser.open(url)
                    logger.info("Opened search: %s", query)
                except Exception as exc:
                    logger.error("Search failed: %s", exc)
            return

        # --- Direct command map ---
        commands: Dict[str, tuple] = {
            "volume up": ("press", "volumeup"),
            "volume down": ("press", "volumedown"),
            "mute": ("press", "volumemute"),
            "screenshot": ("hotkey", "win", "prtsc"),
            "switch window": ("hotkey", "alt", "tab"),
            "close": ("hotkey", "alt", "F4"),
            "scroll up": ("scroll", 10),
            "scroll down": ("scroll", -10),
            "desktop": ("hotkey", "win", "d"),
            "minimize": ("hotkey", "win", "down"),
            "maximize": ("hotkey", "win", "up"),
            "next tab": ("hotkey", "ctrl", "tab"),
            "new tab": ("hotkey", "ctrl", "t"),
            "zoom in": ("hotkey", "ctrl", "="),
            "zoom out": ("hotkey", "ctrl", "-"),
        }

        for cmd_text, action in commands.items():
            if cmd_text in text:
                try:
                    if action[0] == "press":
                        pyautogui.press(action[1], _pause=False)
                    elif action[0] == "hotkey":
                        pyautogui.hotkey(*action[1:], _pause=False)
                    elif action[0] == "scroll":
                        pyautogui.scroll(action[1], _pause=False)
                    logger.info("Voice command executed: %s -> %s", cmd_text, action)
                except Exception as exc:
                    logger.error("Voice command '%s' failed: %s", cmd_text, exc)
                return

        logger.debug("No matching command for: '%s'", text)

    def _open_app(self, app_name: str) -> None:
        """Launch an application by name."""
        custom_paths = self._cfg["CUSTOM_APP_PATHS"]

        # Check custom paths first
        if app_name in custom_paths:
            path = custom_paths[app_name]
            try:
                subprocess.Popen(path, shell=True)
                logger.info("Opened custom app: %s -> %s", app_name, path)
                return
            except Exception as exc:
                logger.error("Failed to open %s: %s", path, exc)

        # Try os.startfile on Windows
        if sys.platform == "win32":
            try:
                os.startfile(app_name)
                logger.info("Opened app via startfile: %s", app_name)
                return
            except OSError:
                pass

        # Fallback: try subprocess
        try:
            subprocess.Popen(app_name, shell=True)
            logger.info("Opened app via subprocess: %s", app_name)
        except Exception as exc:
            logger.error("Could not open app '%s': %s", app_name, exc)
