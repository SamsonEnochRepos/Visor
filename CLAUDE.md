# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What VISOR is

Offline, touchless Windows control system: drive the OS with hand gestures (webcam + MediaPipe) and voice commands (Vosk). Everything runs locally, no cloud. Python, Windows 10/11 only (uses `win32gui`, `os.startfile`, DirectShow camera backend).

Git repository, on branch `main`, with `origin` → `github.com/SamsonEnochRepos/Visor`. The large Vosk model (`models/vosk-model-small-en-us-0.15/`) and runtime logs are gitignored; `assets/hand_landmarker.task` is tracked (it is not auto-downloaded).

## Directory layout

The project was restructured into a `src/` layout. Where things live now:

```
VISOR/
  main.py            Thin launcher — adds src/ to sys.path, calls visor.runtime.main()
  config.json        User-tunable settings (stays at root, easy to find/edit)
  requirements.txt   Pip deps (install.bat reads it from root)
  install.bat        Setup: venv + deps + downloads Vosk model into models/
  VISOR.vbs          Silent pythonw launcher (hardcodes ...\main.py)
  README.md CLAUDE.md
  src/
    config.py        Top-level module, imported as `from config import Config`
    monitor.py       Top-level module, imported as `from monitor import ...`
    visor/           The active v2.0 package (see Architecture below)
      runtime.py     All app/thread/tray orchestration (was the old main.py body)
  assets/
    hand_landmarker.task   MediaPipe model (~7.8MB)
  models/
    vosk-model-small-en-us-0.15/   Vosk STT model
  docs/              Project_description.md, File_structure.md (stale — see below)
  legacy/            Unused v1.0 files (see below)
```

**The import contract that pins this layout:** `config` and `monitor` are imported
as *bare top-level modules* (`from config import Config`, not `from visor.config ...`)
from all over the `visor/` package. That only works because `src/` is on `sys.path`.
`main.py` puts it there; `test_visor.py` does the same. So: **run via `python main.py`
from the project root, never run files inside `src/` directly.** If you add a new
top-level helper module that the package imports bare, it must live in `src/`.

On-disk assets are resolved relative to the project root (computed as `_ROOT`, three
dirs up from `src/visor/runtime.py`, and equivalently in `pipeline.py`/`config.py`).
If you move `runtime.py`, `pipeline.py`, or `config.py` to a different depth, fix those
`os.path.dirname(...)` chains or asset/config lookup breaks.

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

`test_visor.py` is a script with `print`/`PASSED` assertions, **not** a pytest suite — run it directly (from the project root; it adds `src/` to `sys.path` itself). It feeds synthetic landmark arrays through the real classes, so it's the fastest way to validate changes to the `visor/` package without a camera.

Logs (including all caught exceptions) go to `touchless_os.log`. Console only shows WARNING+; the log file has INFO. When debugging a silent failure, read the log file.

## Two codebases live here — only one is active

- **Active (v2.0):** the `src/visor/` package, plus `src/config.py` and `src/monitor.py`. The root `main.py` is a thin launcher into `src/visor/runtime.py`. This is what runs.
- **Legacy (v1.0), unused:** the four files in `legacy/` — `gesture_engine.py`, `voice_engine.py`, `mouse_controller.py`, `overlay.py`. These import each other (and the bare `config`/`monitor` modules) but **nothing in the active path imports them**, and they are *not runnable from `legacy/` as-is* (they assume `config`/`monitor` are importable, which now requires `src/` on the path). They are superseded by `src/visor/`. The docs in `docs/` (`Project_description.md`, `File_structure.md`) and parts of `README.md` still describe this old physics-based architecture — treat them as stale where they conflict with `src/visor/`.

When asked to change gesture/voice/cursor behavior, work in `src/visor/`, not the `legacy/` files.

## Architecture (v2.0, the `visor/` package)

Layered pipeline, one direction of data flow. Canonical data types (`Gesture`, `GestureResult`, `Intent`, `IntentResult`, `LandmarkFrame`) are defined once in [src/visor/core/types.py](src/visor/core/types.py) and passed between layers.

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

### Threading model (in `src/visor/runtime.py`)

Threads coordinate via `threading.Event` flags toggled from the tray menu: `gesture_enabled`, `voice_enabled`, `overlay_visible`, `stop_event`.

- **Pipeline thread** captures camera frames and submits to the tracker. The heavy work (filter → classify → intent → action) runs in the **MediaPipe callback on MP's internal thread** (`_on_detection_result`), guarded by `_processing_lock`. Target: ~5ms in-callback, 60fps camera.
- **Voice thread** (`_run_voice_engine` in `runtime.py`, not in the `visor/` sub-packages) runs the Vosk loop and dispatches via `OSController`.
- **Tray icon thread** (`pystray`).
- **HUD runs on the main thread** — Tkinter requires it.
- `shared_state` is a plain dict written by engines, read by the HUD at ~10Hz.

Because `OSController` is hit from both the gesture callback and the voice thread, it locks internally — keep it that way.

## Configuration

[config.json](config.json) (at the project root) is the single source of user-tunable settings; [src/config.py](src/config.py) is a thread-safe singleton (`Config.get()`) that **hot-reloads** by polling the file mtime every 2s. `config.py` lives in `src/` but resolves `config.json` at the root (parent of `src/`). Every key has a default in `_DEFAULTS` in `config.py` — when adding a config option, add it there too or it won't have a fallback. Access with `cfg["KEY"]` or `cfg.get_value("KEY", default)`.

Monitor mapping: `ACTIVE_MONITOR: -1` maps the hand to the whole virtual desktop (all monitors unified); a non-negative index targets one display. See [src/monitor.py](src/monitor.py).

## Assets required at runtime (not all in repo)

- `assets/hand_landmarker.task` — MediaPipe model (present, ~7.8MB). `pipeline.py` resolves it at `<root>/assets/`.
- `models/vosk-model-small-en-us-0.15/` — Vosk STT model dir. `install.bat` downloads + extracts it into `models/`; the path comes from `VOSK_MODEL_PATH` (now `"models/vosk-model-small-en-us-0.15"`, resolved relative to root). If voice reports "Model not found", this is missing.
