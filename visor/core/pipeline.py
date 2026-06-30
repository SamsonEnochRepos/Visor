"""
pipeline.py — Async pipeline orchestrator for VISOR.

Wires the full perception → recognition → intent → action flow:

    Camera → AsyncHandTracker → LandmarkFilter → TemporalBuffer
           → GestureClassifier → MotionClassifier → ConfidenceScorer
           → IntentEngine → SpatialUIManager / OSController

Architecture:
- Camera thread: captures frames at native FPS
- MediaPipe runs ASYNC via detect_async (no blocking thread needed)
- Results processed in the MP callback → filter → classify → intent → act
- Overlay reads shared_state at 10Hz (unchanged)

Target: 60 FPS camera, <40ms end-to-end latency.
"""

import os
import logging
import threading
import time
from typing import Dict, Any, Optional, Tuple

import numpy as np

from config import Config
from monitor import detect_monitors, get_mapping_region

from visor.core.types import (
    Gesture, GestureResult, Intent, IntentResult, LandmarkFrame,
)
from visor.core.events import get_event_bus, EVENT_GESTURE_DETECTED, EVENT_INTENT_RESOLVED
from visor.input.camera_provider import WebcamProvider, CameraProvider
from visor.perception.hand_tracker import AsyncHandTracker, HandDetectionResult
from visor.perception.landmark_filter import LandmarkFilter
from visor.perception.temporal_buffer import TemporalBuffer
from visor.recognition.gesture_classifier import GestureClassifier
from visor.recognition.motion_classifier import MotionClassifier
from visor.recognition.confidence import ConfidenceScorer
from visor.intent.intent_engine import IntentEngine
from visor.intent.context_manager import ContextManager
from visor.action.os_controller import OSController
from visor.action.spatial_ui import SpatialUIManager

logger = logging.getLogger("VISOR.pipeline")


class Pipeline:
    """Full async perception → recognition → intent → action pipeline.

    This replaces the old GestureEngine and its 3-thread queue-based
    architecture with a cleaner async design:

    1. Camera thread captures frames and submits to AsyncHandTracker
    2. MediaPipe callback fires on MP's internal thread
    3. Callback runs the full classify → intent → action chain (~5ms)
    4. No queue bottleneck, no extra threads needed
    """

    def __init__(self, shared_state: Dict[str, Any],
                 enabled_event: threading.Event,
                 stop_event: threading.Event) -> None:
        self._shared = shared_state
        self._enabled = enabled_event
        self._stop = stop_event
        self._cfg = Config.get()
        self._event_bus = get_event_bus()

        # --- Monitor mapping ---
        monitors = detect_monitors()
        active_idx = self._cfg.get_value("ACTIVE_MONITOR", -1)
        region = get_mapping_region(monitors, active_idx)
        self._screen_x = region.x
        self._screen_y = region.y
        self._screen_w = region.width
        self._screen_h = region.height

        # --- Input ---
        self._camera: CameraProvider = WebcamProvider(
            camera_index=self._cfg.get_value("CAMERA_INDEX", 0),
            width=self._cfg.get_value("FRAME_WIDTH", 640),
            height=self._cfg.get_value("FRAME_HEIGHT", 480),
            target_fps=self._cfg.get_value("TARGET_FPS", 60),
        )

        # --- Perception ---
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(script_dir))
        model_path = os.path.join(project_root, "hand_landmarker.task")

        self._tracker: Optional[AsyncHandTracker] = None
        self._model_path = model_path
        self._filters: Dict[str, LandmarkFilter] = {
            "Left": LandmarkFilter(
                min_cutoff=self._cfg.get_value("FILTER_MIN_CUTOFF", 1.0),
                beta=self._cfg.get_value("FILTER_BETA", 0.007),
            ),
            "Right": LandmarkFilter(
                min_cutoff=self._cfg.get_value("FILTER_MIN_CUTOFF", 1.0),
                beta=self._cfg.get_value("FILTER_BETA", 0.007),
            ),
        }
        self._buffers: Dict[str, TemporalBuffer] = {
            "Left": TemporalBuffer(max_frames=60),
            "Right": TemporalBuffer(max_frames=60),
        }

        # --- Recognition ---
        self._gesture_classifier = GestureClassifier()
        self._motion_classifier = MotionClassifier()
        self._confidence_scorers: Dict[str, ConfidenceScorer] = {
            "Left": ConfidenceScorer(
                window_size=self._cfg.get_value("STABILITY_WINDOW_FRAMES", 12),
                min_confidence=self._cfg.get_value("MIN_GESTURE_CONFIDENCE", 0.55),
                min_stability=self._cfg.get_value("MIN_GESTURE_STABILITY", 0.45),
            ),
            "Right": ConfidenceScorer(
                window_size=self._cfg.get_value("STABILITY_WINDOW_FRAMES", 12),
                min_confidence=self._cfg.get_value("MIN_GESTURE_CONFIDENCE", 0.55),
                min_stability=self._cfg.get_value("MIN_GESTURE_STABILITY", 0.45),
            ),
        }

        # --- Intent ---
        self._context = ContextManager()
        self._intent_engine = IntentEngine()

        # --- Action ---
        self._os = OSController()
        self._spatial_ui = SpatialUIManager(
            self._os,
            screen_width=self._screen_w,
            screen_height=self._screen_h,
        )

        # --- State ---
        self._prev_gesture: Optional[GestureResult] = None
        self._processing_lock = threading.Lock()
        self._frame_count: int = 0
        self._fps_counter: int = 0
        self._fps_timer: float = 0.0
        self._had_hands: bool = False

    def run(self) -> None:
        """Main pipeline loop — call from a thread."""
        logger.info("Pipeline starting")
        self._shared["gesture_status"] = "Opening camera..."

        # --- Open camera ---
        if not self._camera.start():
            self._shared["gesture_status"] = "Camera not found"
            logger.error("Camera failed to open")
            return

        # --- Init hand tracker ---
        self._shared["gesture_status"] = "Loading hand model..."
        try:
            self._tracker = AsyncHandTracker(
                model_path=self._model_path,
                num_hands=self._cfg.get_value("NUM_HANDS", 2),
                detection_confidence=self._cfg.get_value(
                    "DETECTION_CONFIDENCE", 0.6
                ),
                tracking_confidence=self._cfg.get_value(
                    "TRACKING_CONFIDENCE", 0.5
                ),
                on_result=self._on_detection_result,
            )
        except FileNotFoundError as exc:
            logger.error("Model not found: %s", exc)
            self._shared["gesture_status"] = "Model not found"
            self._camera.stop()
            return
        except Exception as exc:
            logger.error("Hand tracker init failed: %s", exc)
            self._shared["gesture_status"] = f"Tracker error: {exc}"
            self._camera.stop()
            return

        self._shared["gesture_status"] = "Running"
        self._fps_timer = time.monotonic()
        logger.info("Pipeline started — camera + async tracker ready")

        # --- Camera capture loop ---
        try:
            while not self._stop.is_set():
                if not self._enabled.is_set():
                    self._shared["gesture_status"] = "Paused"
                    self._stop.wait(timeout=0.05)
                    continue

                result = self._camera.read_frame()
                if result is None:
                    time.sleep(0.001)
                    continue

                rgb_frame, timestamp = result
                self._tracker.submit_frame(rgb_frame, timestamp)
                self._frame_count += 1

                # Brief yield to prevent CPU spin
                time.sleep(0.001)

        except Exception as exc:
            logger.error("Pipeline crashed: %s", exc)
            self._shared["gesture_status"] = f"Error: {exc}"
        finally:
            if self._tracker:
                self._tracker.close()
            self._camera.stop()
            logger.info("Pipeline stopped after %d frames", self._frame_count)

    def _on_detection_result(self, detection: HandDetectionResult) -> None:
        """Process a hand detection result from the async tracker.

        This runs on MediaPipe's internal thread. The full chain
        (filter → classify → intent → action) should complete in <5ms.
        """
        t0 = time.perf_counter()

        with self._processing_lock:
            if not detection.has_hands:
                if self._had_hands:
                    # Hand was lost — transition to idle
                    self._shared["gesture_state"] = "none"
                    self._shared["gesture_confidence"] = "0%"
                    self._shared["gesture_stability"] = "0%"
                    self._had_hands = False
                    self._intent_engine.reset()
                    self._event_bus.publish("hand.lost")
                self._update_fps(t0)
                return

            if not self._had_hands:
                self._had_hands = True
                self._event_bus.publish("hand.found")

            # Process the primary hand (first detected)
            # TODO: Multi-hand fusion for future
            for i in range(detection.num_hands):
                landmarks = detection.hand_landmarks[i]
                label = detection.handedness_labels[i]
                timestamp = detection.timestamp

                # 1. Filter landmarks (One Euro)
                filt = self._filters.get(label)
                if filt is None:
                    filt = LandmarkFilter()
                    self._filters[label] = filt
                filtered = filt.filter_landmarks(landmarks, timestamp)

                # 2. Push to temporal buffer
                frame = LandmarkFrame(
                    landmarks=filtered,
                    handedness=label,
                    timestamp=timestamp,
                    raw_landmarks=landmarks,
                )
                buf = self._buffers.get(label)
                if buf is None:
                    buf = TemporalBuffer()
                    self._buffers[label] = buf
                buf.push(frame)

                # 3. Classify static gesture
                gesture = self._gesture_classifier.classify(filtered, label)

                # 4. Overlay motion classification (swipe/push)
                gesture = self._motion_classifier.classify(buf, gesture)

                # 5. Score confidence + stability
                scorer = self._confidence_scorers.get(label)
                if scorer is None:
                    scorer = ConfidenceScorer()
                    self._confidence_scorers[label] = scorer
                gesture = scorer.score(gesture)

                # 6. Update shared state for HUD
                self._shared["gesture_state"] = gesture.gesture.value
                self._shared["gesture_confidence"] = f"{gesture.confidence:.0%}"
                self._shared["gesture_stability"] = f"{gesture.stability:.0%}"

                # 7. Map cursor to screen coordinates
                cursor_x, cursor_y = self._map_to_screen(gesture.cursor_pos)

                # 8. Always move cursor for POINT gesture (even below threshold)
                if gesture.gesture == Gesture.POINT and gesture.confidence > 0.3:
                    self._os.move_cursor(cursor_x, cursor_y)

                # 9. Gate: only execute actions if confident + stable
                if not scorer.should_act(gesture):
                    # Still move cursor for CURSOR_MOVE intent
                    if gesture.gesture == Gesture.POINT:
                        self._os.move_cursor(cursor_x, cursor_y)
                    continue

                # 10. Resolve intent
                ctx = self._context.get_context((cursor_x, cursor_y))
                intent = self._intent_engine.resolve(gesture, ctx)

                # 11. Update HUD
                self._shared["intent"] = intent.intent.value

                # 12. Execute action
                self._spatial_ui.handle_intent(intent, cursor_x, cursor_y)

                # 13. Publish events
                self._event_bus.publish(EVENT_GESTURE_DETECTED, gesture)
                self._event_bus.publish(EVENT_INTENT_RESOLVED, intent)

                self._prev_gesture = gesture

                # Only process primary hand for now
                break

        self._update_fps(t0)

    def _map_to_screen(self, cursor_pos: Tuple[float, float]) -> Tuple[int, int]:
        """Map normalized cursor position (0-1) to screen pixel coordinates."""
        nx = max(0.0, min(1.0, cursor_pos[0]))
        ny = max(0.0, min(1.0, cursor_pos[1]))
        x = int(self._screen_x + nx * self._screen_w)
        y = int(self._screen_y + ny * self._screen_h)
        return x, y

    def _update_fps(self, t0: float) -> None:
        """Update FPS and latency counters."""
        latency_ms = (time.perf_counter() - t0) * 1000.0
        self._fps_counter += 1
        now = time.monotonic()
        elapsed = now - self._fps_timer
        if elapsed >= 1.0:
            fps = self._fps_counter / elapsed
            self._shared["fps"] = f"{fps:.0f} ({latency_ms:.0f}ms)"
            self._shared["gesture_status"] = "Running"
            self._fps_counter = 0
            self._fps_timer = now
