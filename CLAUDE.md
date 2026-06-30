# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What VISOR is

Offline, touchless Windows control system: drive the OS with hand gestures (webcam + MediaPipe) and voice commands (Vosk). Everything runs locally, no cloud. Python, Windows 10/11 only (uses `win32gui`, `os.startfile`, DirectShow camera backend).

This is not a git repository.

## Commands

```bat
:: First-time setup — creates venv, installs deps, downloads Vosk model
install.bat

:: Run (foreground, with console + tray)
python main.py

:: Run silently in tray (production launch) — uses venv\Scripts\pythonw.exe
:: double-click VISOR.vbs   (hardcodes the absolute project path)

:: Functional smoke test of perception → recognition → intent layers
python test_visor.py
```

`test_visor.py` is a script with `print`/`PASSED` assertions, **not** a pytest suite — run it directly. It feeds synthetic landmark arrays through the real classes, so it's the fastest way to validate changes to the `visor/` package without a camera.

Logs (including all caught exceptions) go to `touchless_os.log`. Console only shows WARNING+; the log file has INFO. When debugging a silent failure, read the log file.

## Two codebases live here — only one is active

- **Active (v2.0):** the `visor/` package. `main.py` imports exclusively from `visor/`. This is what runs.
- **Legacy (v1.0), unused:** root-level `gesture_engine.py`, `voice_engine.py`, `mouse_controller.py`, `overlay.py`. These import each other but **nothing in the active path imports them**. They are superseded by `visor/`. `Project_description.md` and parts of `README.md`/`File_structure.md` still describe this old physics-based architecture — treat those docs as stale where they conflict with `visor/`.

When asked to change gesture/voice/cursor behavior, work in `visor/`, not the root `.py` files.

## Architecture (v2.0, the `visor/` package)

Layered pipeline, one direction of data flow. Canonical data types (`Gesture`, `GestureResult`, `Intent`, `IntentResult`, `LandmarkFrame`) are defined once in [visor/core/types.py](visor/core/types.py) and passed between layers.

```
Camera → AsyncHandTracker → LandmarkFilter → TemporalBuffer
       → GestureClassifier → MotionClassifier → ConfidenceScorer
       → IntentEngine → SpatialUIManager / OSController
```

- **input/** — `WebcamProvider` (camera) and `PyAudioProvider` (mic).
- **perception/** — `AsyncHandTracker` wraps MediaPipe HandLandmarker in **LIVE_STREAM async mode** (`detect_async` + callback, not blocking `detect`). `LandmarkFilter` is a One Euro filter (one instance per hand). `TemporalBuffer` keeps the last N frames for velocity/displacement queries.
- **recognition/** — `GestureClassifier` is stateless and **geometry-only** (no velocity/frame-rate dependence): every gesture gets a soft score in [0,1], highest wins. `MotionClassifier` overlays motion gestures (swipe/push) using the buffer. `ConfidenceScorer` tracks temporal stability and gates actions via `should_act()`.
- **intent/** — `IntentEngine` is the central decoupling layer: turns `GestureResult` + OS context into an `IntentResult`, holding the small amount of cross-frame state needed for sequences (e.g. quick pinch = SELECT, held pinch = DRAG). `VoiceIntentResolver` maps recognized text → `IntentResult`. `ContextManager` supplies cursor-location context.
- **action/** — `OSController` is a thin, **thread-safe** facade over `pyautogui` + `win32gui` with *no* gesture logic. `SpatialUIManager` translates intents into window grab/move/resize/snap/throw.
- **core/** — `pipeline.py` (orchestrator), `events.py` (process-wide singleton `EventBus`, sync pub/sub), `types.py`.
- **ui/** — `HUD`, a transparent Tkinter overlay reading `shared_state`.

### Threading model (in `main.py`)

Threads coordinate via `threading.Event` flags toggled from the tray menu: `gesture_enabled`, `voice_enabled`, `overlay_visible`, `stop_event`.

- **Pipeline thread** captures camera frames and submits to the tracker. The heavy work (filter → classify → intent → action) runs in the **MediaPipe callback on MP's internal thread** (`_on_detection_result`), guarded by `_processing_lock`. Target: ~5ms in-callback, 60fps camera.
- **Voice thread** (`_run_voice_engine` in `main.py`, not in `visor/`) runs the Vosk loop and dispatches via `OSController`.
- **Tray icon thread** (`pystray`).
- **HUD runs on the main thread** — Tkinter requires it.
- `shared_state` is a plain dict written by engines, read by the HUD at ~10Hz.

Because `OSController` is hit from both the gesture callback and the voice thread, it locks internally — keep it that way.

## Configuration

[config.json](config.json) is the single source of user-tunable settings; [config.py](config.py) is a thread-safe singleton (`Config.get()`) that **hot-reloads** by polling the file mtime every 2s. Every key has a default in `_DEFAULTS` in `config.py` — when adding a config option, add it there too or it won't have a fallback. Access with `cfg["KEY"]` or `cfg.get_value("KEY", default)`.

Monitor mapping: `ACTIVE_MONITOR: -1` maps the hand to the whole virtual desktop (all monitors unified); a non-negative index targets one display. See [monitor.py](monitor.py).

## Assets required at runtime (not all in repo)

- `hand_landmarker.task` — MediaPipe model, lives at project root (present, ~7.8MB).
- `vosk-model-small-en-us-0.15/` — Vosk STT model dir. `install.bat` downloads it; path is set by `VOSK_MODEL_PATH`. If voice reports "Model not found", this is missing.
