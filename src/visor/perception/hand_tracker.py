"""
hand_tracker.py — Asynchronous MediaPipe HandLandmarker wrapper.

Wraps MediaPipe's HandLandmarker Tasks API with async inference
(LIVE_STREAM mode) for non-blocking hand detection. Targets 60fps
with <40ms latency.

Key improvement over old code:
- Uses detect_async() instead of blocking detect()
- Configured for 2 hands (fixes the old num_hands=1 bug)
- Callback-based architecture eliminates the mediapipe thread
"""

import os
import logging
import threading
import time
from typing import Callable, Optional, List, Tuple

import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

logger = logging.getLogger("VISOR.perception.tracker")


class HandDetectionResult:
    """Processed result from a single hand detection frame."""

    def __init__(self, hand_landmarks: List[np.ndarray],
                 handedness_labels: List[str],
                 timestamp: float) -> None:
        self.hand_landmarks = hand_landmarks  # List of 21x3 ndarrays
        self.handedness_labels = handedness_labels  # List of "Left"/"Right"
        self.timestamp = timestamp  # Monotonic seconds
        self.num_hands = len(hand_landmarks)

    @property
    def has_hands(self) -> bool:
        return self.num_hands > 0


class AsyncHandTracker:
    """Asynchronous MediaPipe HandLandmarker with frame timestamping.

    Uses LIVE_STREAM running mode for non-blocking async detection.
    Results are delivered via a callback, eliminating the need for
    a dedicated MediaPipe processing thread.
    """

    def __init__(self, model_path: str,
                 num_hands: int = 2,
                 detection_confidence: float = 0.6,
                 presence_confidence: float = 0.6,
                 tracking_confidence: float = 0.5,
                 on_result: Optional[Callable[[HandDetectionResult], None]] = None) -> None:
        """Initialize the async hand tracker.

        Args:
            model_path: Path to hand_landmarker.task model file.
            num_hands: Maximum number of hands to detect (default 2).
            detection_confidence: Min confidence for initial detection.
            presence_confidence: Min confidence for hand presence.
            tracking_confidence: Min confidence for landmark tracking.
            on_result: Callback invoked with each detection result.
        """
        self._model_path = model_path
        self._on_result = on_result
        self._landmarker: Optional[mp_vision.HandLandmarker] = None
        self._lock = threading.Lock()
        self._frame_count: int = 0
        self._last_timestamp_ms: int = 0

        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"Hand landmarker model not found: {model_path}"
            )

        # Configure for async (LIVE_STREAM) mode
        base_options = mp_python.BaseOptions(
            model_asset_path=model_path,
        )
        options = mp_vision.HandLandmarkerOptions(
            base_options=base_options,
            running_mode=mp_vision.RunningMode.LIVE_STREAM,
            num_hands=num_hands,
            min_hand_detection_confidence=detection_confidence,
            min_hand_presence_confidence=presence_confidence,
            min_tracking_confidence=tracking_confidence,
            result_callback=self._mp_callback,
        )

        self._landmarker = mp_vision.HandLandmarker.create_from_options(options)
        logger.info(
            "AsyncHandTracker initialized: num_hands=%d, det=%.2f, track=%.2f",
            num_hands, detection_confidence, tracking_confidence,
        )

    def submit_frame(self, rgb_frame: np.ndarray, timestamp: float) -> None:
        """Submit a frame for async detection.

        Non-blocking: the result will arrive via the on_result callback.

        Args:
            rgb_frame: RGB uint8 numpy array (H, W, 3).
            timestamp: Monotonic timestamp in seconds.
        """
        if self._landmarker is None:
            return

        # MediaPipe requires strictly increasing integer timestamps in ms
        timestamp_ms = int(timestamp * 1000)
        with self._lock:
            if timestamp_ms <= self._last_timestamp_ms:
                timestamp_ms = self._last_timestamp_ms + 1
            self._last_timestamp_ms = timestamp_ms

        try:
            mp_image = mp.Image(
                image_format=mp.ImageFormat.SRGB, data=rgb_frame
            )
            self._landmarker.detect_async(mp_image, timestamp_ms)
            self._frame_count += 1
        except Exception as exc:
            logger.error("detect_async failed: %s", exc)

    def _mp_callback(self, result, output_image, timestamp_ms: int) -> None:
        """Internal callback from MediaPipe — runs on MP's thread."""
        try:
            if not result.hand_landmarks:
                detection = HandDetectionResult([], [], timestamp_ms / 1000.0)
            else:
                landmarks_list = []
                labels = []

                for i, hand_lms in enumerate(result.hand_landmarks):
                    # Convert MediaPipe landmarks to 21x3 numpy array
                    lm_array = np.array(
                        [[lm.x, lm.y, lm.z] for lm in hand_lms],
                        dtype=np.float64,
                    )
                    landmarks_list.append(lm_array)

                    # Extract handedness label
                    if result.handedness and i < len(result.handedness):
                        labels.append(
                            result.handedness[i][0].category_name
                        )
                    else:
                        labels.append("Right")

                detection = HandDetectionResult(
                    landmarks_list, labels, timestamp_ms / 1000.0
                )

            if self._on_result is not None:
                self._on_result(detection)

        except Exception as exc:
            logger.error("Result callback error: %s", exc)

    def close(self) -> None:
        """Release MediaPipe resources."""
        if self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None
            logger.info(
                "AsyncHandTracker closed after %d frames", self._frame_count
            )

    @property
    def frame_count(self) -> int:
        return self._frame_count


class SyncHandTracker:
    """Synchronous fallback tracker for testing and debugging.

    Uses VIDEO running mode (synchronous detect) instead of LIVE_STREAM.
    Useful when you need deterministic frame-by-frame processing.
    """

    def __init__(self, model_path: str,
                 num_hands: int = 2,
                 detection_confidence: float = 0.6,
                 tracking_confidence: float = 0.5) -> None:

        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"Hand landmarker model not found: {model_path}"
            )

        base_options = mp_python.BaseOptions(
            model_asset_path=model_path,
        )
        options = mp_vision.HandLandmarkerOptions(
            base_options=base_options,
            running_mode=mp_vision.RunningMode.VIDEO,
            num_hands=num_hands,
            min_hand_detection_confidence=detection_confidence,
            min_hand_presence_confidence=detection_confidence,
            min_tracking_confidence=tracking_confidence,
        )
        self._landmarker = mp_vision.HandLandmarker.create_from_options(options)
        self._last_timestamp_ms: int = 0

    def detect(self, rgb_frame: np.ndarray,
               timestamp: float) -> HandDetectionResult:
        """Synchronously detect hands in a frame."""
        timestamp_ms = int(timestamp * 1000)
        if timestamp_ms <= self._last_timestamp_ms:
            timestamp_ms = self._last_timestamp_ms + 1
        self._last_timestamp_ms = timestamp_ms

        mp_image = mp.Image(
            image_format=mp.ImageFormat.SRGB, data=rgb_frame
        )
        result = self._landmarker.detect_for_video(mp_image, timestamp_ms)

        if not result.hand_landmarks:
            return HandDetectionResult([], [], timestamp)

        landmarks_list = []
        labels = []
        for i, hand_lms in enumerate(result.hand_landmarks):
            lm_array = np.array(
                [[lm.x, lm.y, lm.z] for lm in hand_lms],
                dtype=np.float64,
            )
            landmarks_list.append(lm_array)
            if result.handedness and i < len(result.handedness):
                labels.append(result.handedness[i][0].category_name)
            else:
                labels.append("Right")

        return HandDetectionResult(landmarks_list, labels, timestamp)

    def close(self) -> None:
        if self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None
