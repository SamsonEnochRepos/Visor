"""Dynamic gesture classifier — swipe and push detection.

Analyses the motion trajectory of the palm-centre anchor point
(MIDDLE_MCP, index 9) across the most recent frames stored in the
``TemporalBuffer``.  Three dynamic gestures are recognised:

* **SWIPE_LEFT / SWIPE_RIGHT** — fast, straight horizontal movement.
* **PUSH** — forward thrust toward the camera (requires z-coordinates).

If the trajectory does not satisfy any dynamic-gesture criteria the
static ``GestureResult`` produced by the upstream classifier is
returned unchanged.

Design notes
────────────
* A minimum of 10 frames must be buffered before analysis runs.
  This prevents false positives during startup or hand re-entry.
* *Directness* (displacement / path-length) is the primary quality
  filter — only straight-line motions qualify as intentional swipes.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional, Tuple

import numpy as np

from visor.core.types import Gesture, GestureResult, LandmarkFrame
from visor.perception.temporal_buffer import TemporalBuffer

logger = logging.getLogger("VISOR.recognition.motion_classifier")

# The landmark tracked for trajectory analysis.
_ANCHOR_INDEX: int = 9  # MIDDLE_MCP

# ── Swipe thresholds ──────────────────────────────────────────────────
_MIN_FRAMES: int = 10
_TRAJECTORY_WINDOW: int = 15
_SWIPE_DIRECTNESS: float = 0.65
_SWIPE_MIN_DISPLACEMENT: float = 0.12
_SWIPE_AXIS_RATIO: float = 1.8  # |Δx| must be > |Δy| × this factor

# ── Push thresholds ───────────────────────────────────────────────────
_PUSH_Z_DISPLACEMENT: float = -0.04  # negative = toward camera
_PUSH_DIRECTNESS: float = 0.50


class MotionClassifier:
    """Detects dynamic gestures from temporal landmark trajectories.

    Usage::

        mc = MotionClassifier()
        result = mc.classify(temporal_buffer, static_gesture_result)
    """

    # ── public API ────────────────────────────────────────────────────

    def classify(
        self,
        buffer: TemporalBuffer,
        static_gesture: GestureResult,
    ) -> GestureResult:
        """Analyse recent motion and, if a dynamic gesture is found,
        return a new ``GestureResult`` that overrides the static one.

        Args:
            buffer: The temporal buffer holding recent
                ``LandmarkFrame`` instances.
            static_gesture: The result from the static
                ``GestureClassifier`` for the current frame.

        Returns:
            Either a new ``GestureResult`` for the detected dynamic
            gesture, or ``static_gesture`` if no dynamic motion
            qualifies.
        """
        frames: List[LandmarkFrame] = buffer.get_window(_TRAJECTORY_WINDOW)

        if len(frames) < _MIN_FRAMES:
            return static_gesture

        # Build trajectory of the anchor point.
        trajectory: List[np.ndarray] = []
        for frame in frames:
            lm = frame.landmarks
            if lm is not None and len(lm) > _ANCHOR_INDEX:
                trajectory.append(lm[_ANCHOR_INDEX])

        if len(trajectory) < _MIN_FRAMES:
            return static_gesture

        # ── Trajectory metrics ────────────────────────────────────────
        first = trajectory[0]
        last = trajectory[-1]
        displacement: np.ndarray = last[:2] - first[:2]
        displacement_mag: float = float(np.linalg.norm(displacement))

        path_length: float = 0.0
        for i in range(1, len(trajectory)):
            seg = trajectory[i][:2] - trajectory[i - 1][:2]
            path_length += float(np.linalg.norm(seg))

        if path_length < 1e-7:
            return static_gesture

        directness: float = displacement_mag / path_length

        # ── SWIPE detection ───────────────────────────────────────────
        swipe_result = self._detect_swipe(
            displacement,
            displacement_mag,
            directness,
            static_gesture,
        )
        if swipe_result is not None:
            return swipe_result

        # ── PUSH detection ────────────────────────────────────────────
        has_z = all(t.shape[-1] >= 3 for t in trajectory)
        if has_z:
            push_result = self._detect_push(
                trajectory,
                directness,
                static_gesture,
            )
            if push_result is not None:
                return push_result

        return static_gesture

    # ── private detection helpers ─────────────────────────────────────

    @staticmethod
    def _detect_swipe(
        displacement: np.ndarray,
        displacement_mag: float,
        directness: float,
        static_gesture: GestureResult,
    ) -> Optional[GestureResult]:
        """Return a swipe ``GestureResult`` if the trajectory qualifies.

        Criteria (all must be satisfied):
            1. ``directness > 0.65``  — path is reasonably straight.
            2. ``|Δx| > 0.12``        — sufficient horizontal travel.
            3. ``|Δx| > |Δy| × 1.8`` — predominantly horizontal.

        Returns:
            ``GestureResult`` for SWIPE_LEFT or SWIPE_RIGHT, or
            ``None`` if no swipe is detected.
        """
        dx: float = float(displacement[0])
        dy: float = float(displacement[1])
        abs_dx: float = abs(dx)
        abs_dy: float = abs(dy)

        if directness <= _SWIPE_DIRECTNESS:
            return None
        if abs_dx <= _SWIPE_MIN_DISPLACEMENT:
            return None
        if abs_dx <= abs_dy * _SWIPE_AXIS_RATIO:
            return None

        confidence = min(1.0, directness * displacement_mag * 4.0)
        gesture = Gesture.SWIPE_RIGHT if dx > 0 else Gesture.SWIPE_LEFT

        logger.debug(
            "Swipe detected: %s  confidence=%.2f  dx=%.3f  directness=%.2f",
            gesture.name,
            confidence,
            dx,
            directness,
        )

        return GestureResult(
            gesture=gesture,
            confidence=confidence,
            stability=0.0,
            cursor_pos=static_gesture.cursor_pos,
            landmarks=static_gesture.landmarks,
            timestamp=time.monotonic(),
        )

    @staticmethod
    def _detect_push(
        trajectory: List[np.ndarray],
        directness: float,
        static_gesture: GestureResult,
    ) -> Optional[GestureResult]:
        """Return a push ``GestureResult`` if the z-trajectory qualifies.

        Criteria:
            1. z-displacement < −0.04 (moving *toward* the camera in
               MediaPipe's coordinate frame where z decreases as the
               hand approaches the lens).
            2. ``directness > 0.50``.

        Returns:
            ``GestureResult`` for PUSH, or ``None``.
        """
        if directness <= _PUSH_DIRECTNESS:
            return None

        z_first: float = float(trajectory[0][2])
        z_last: float = float(trajectory[-1][2])
        z_disp: float = z_last - z_first

        if z_disp >= _PUSH_Z_DISPLACEMENT:
            return None

        confidence = min(1.0, abs(z_disp) * 12.0)

        logger.debug(
            "Push detected:  confidence=%.2f  z_disp=%.4f  directness=%.2f",
            confidence,
            z_disp,
            directness,
        )

        return GestureResult(
            gesture=Gesture.PUSH,
            confidence=confidence,
            stability=0.0,
            cursor_pos=static_gesture.cursor_pos,
            landmarks=static_gesture.landmarks,
            timestamp=time.monotonic(),
        )
