"""Landmark-geometry-based gesture classifier.

Maps 21-point hand landmarks to one of eight static gestures using
soft-scoring functions.  Each scoring function returns a float in
[0.0, 1.0]; the gesture with the highest score wins, provided it
clears the minimum-confidence gate.

Design rationale
────────────────
* **No velocity thresholds** — only landmark geometry is considered,
  making classification independent of frame rate or hand speed.
* **Soft scoring** — every candidate gesture gets a continuous score
  instead of a hard if/elif ladder, which makes the classifier easy
  to tune and extend.
* **Unified anchor** — MIDDLE_MCP (index 9, palm center) is used as
  the cursor reference point for all downstream layers.
"""

from __future__ import annotations

import logging
import math
import time
from typing import List, Optional, Tuple

import numpy as np

from visor.core.types import Gesture, GestureResult

logger = logging.getLogger("VISOR.recognition.gesture_classifier")

# ── Landmark indices (MediaPipe 21-point hand model) ──────────────────
WRIST = 0
THUMB_TIP = 4
THUMB_IP = 3
INDEX_TIP = 8
INDEX_PIP = 6
MIDDLE_MCP = 9
MIDDLE_TIP = 12
MIDDLE_PIP = 10
RING_TIP = 16
RING_PIP = 14
PINKY_TIP = 20
PINKY_PIP = 18

# The palm-centre landmark used as the canonical cursor anchor.
ANCHOR_LANDMARK = MIDDLE_MCP

# If the best score is below this, we return Gesture.NONE.
_MIN_CONFIDENCE: float = 0.40


class GestureClassifier:
    """Stateless classifier that scores static hand poses.

    Usage::

        classifier = GestureClassifier()
        result = classifier.classify(landmarks_21x3, "Right")
    """

    # ── public API ────────────────────────────────────────────────────

    def classify(
        self,
        landmarks: np.ndarray,
        handedness: str,
    ) -> GestureResult:
        """Classify a single frame of hand landmarks into a gesture.

        Args:
            landmarks: NumPy array of shape ``(21, 2)`` or ``(21, 3)``
                containing normalised landmark coordinates.
            handedness: ``"Left"`` or ``"Right"`` — required for
                thumb-extension logic which is mirror-dependent.

        Returns:
            A ``GestureResult`` with the best matching gesture, its
            confidence, and the cursor position derived from the
            anchor landmark.
        """
        fingers: List[bool] = self._finger_extension(landmarks, handedness)
        pinch_dist: float = self._normalized_distance(landmarks, THUMB_TIP, INDEX_TIP)
        curl: float = self._average_curl(landmarks)
        anchor: Tuple[float, float] = self._get_anchor(landmarks)

        # --- score every candidate gesture ---------------------------------
        scores: List[Tuple[Gesture, float]] = [
            (Gesture.POINT, self._score_point(fingers)),
            (Gesture.PINCH, self._score_pinch(pinch_dist, fingers)),
            (Gesture.GRAB, self._score_grab(curl, fingers)),
            (Gesture.OPEN_PALM, self._score_palm(fingers, landmarks)),
            (Gesture.FIST, self._score_fist(fingers, curl)),
        ]

        best_gesture, best_confidence = max(scores, key=lambda t: t[1])

        if best_confidence < _MIN_CONFIDENCE:
            best_gesture = Gesture.NONE
            best_confidence = 0.0

        return GestureResult(
            gesture=best_gesture,
            confidence=best_confidence,
            stability=0.0,  # filled in by ConfidenceScorer downstream
            cursor_pos=anchor,
            landmarks=landmarks,
            timestamp=time.monotonic(),
        )

    # ── scoring functions (each returns 0.0 – 1.0) ───────────────────

    @staticmethod
    def _score_point(fingers: List[bool]) -> float:
        """Score for the *point* gesture (index extended, rest curled).

        Breakdown:
            +0.40  index finger extended
            +0.15  thumb curled
            +0.15  middle curled
            +0.15  ring curled
            +0.15  pinky curled
        """
        score = 0.0
        thumb, index, middle, ring, pinky = fingers

        if index:
            score += 0.40
        if not thumb:
            score += 0.15
        if not middle:
            score += 0.15
        if not ring:
            score += 0.15
        if not pinky:
            score += 0.15
        return score

    @staticmethod
    def _score_pinch(pinch_dist: float, fingers: List[bool]) -> float:
        """Score for the *pinch* gesture (thumb + index tips touching).

        Uses a sigmoid centred at ``0.045`` normalised units so the
        transition from "touching" to "apart" is smooth rather than
        a hard threshold.

        A small bonus is added when the remaining fingers are relaxed
        (not fully clenched), which distinguishes a pinch from a fist
        that happens to have thumb-index contact.
        """
        # Sigmoid: approaches 1.0 when pinch_dist → 0
        base = 1.0 / (1.0 + math.exp(25.0 * (pinch_dist - 0.045)))

        _thumb, _index, middle, ring, pinky = fingers
        relaxed_count = sum([middle, ring, pinky])
        bonus = 0.10 * (relaxed_count / 3.0)  # up to +0.10

        return min(1.0, base + bonus)

    @staticmethod
    def _score_grab(curl: float, fingers: List[bool]) -> float:
        """Score for the *grab* gesture (all fingers partially curled).

        The ideal curl range is [0.3, 0.8] — a loose claw shape.
        Fully open or fully clenched hands are penalised.  An extra
        bonus is given when the thumb is also tucked in.
        """
        # Peak at curl ≈ 0.55, drops toward 0 and 1.
        if 0.3 <= curl <= 0.8:
            base = 1.0 - 2.0 * abs(curl - 0.55)
            base = max(0.0, base)
        else:
            base = 0.0

        # Penalise if any finger is fully extended.
        extended_count = sum(fingers)
        if extended_count >= 3:
            base *= 0.3

        # Thumb-tucked bonus.
        thumb = fingers[0]
        if not thumb:
            base = min(1.0, base + 0.15)

        return min(1.0, base)

    @staticmethod
    def _score_palm(fingers: List[bool], landmarks: np.ndarray) -> float:
        """Score for the *open palm* gesture (all five fingers extended).

        Base score of 0.60 when all fingers are up.  An additional
        spread bonus (up to 0.40) is calculated from the average
        distance between adjacent fingertips — a wider spread
        increases confidence.
        """
        extended_count = sum(fingers)
        if extended_count < 5:
            # Partial credit: linearly scale with number of fingers.
            return max(0.0, (extended_count / 5.0) * 0.35)

        base = 0.60

        # Finger-spread bonus: mean distance between adjacent tips.
        tip_indices = [THUMB_TIP, INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP]
        spread_distances: List[float] = []
        for i in range(len(tip_indices) - 1):
            a = landmarks[tip_indices[i]][:2]
            b = landmarks[tip_indices[i + 1]][:2]
            spread_distances.append(float(np.linalg.norm(a - b)))

        mean_spread = float(np.mean(spread_distances)) if spread_distances else 0.0
        # Normalise — typical spread is ~0.06‥0.14 in normalised coords.
        spread_bonus = min(0.40, mean_spread * 3.0)

        return min(1.0, base + spread_bonus)

    @staticmethod
    def _score_fist(fingers: List[bool], curl: float) -> float:
        """Score for the *fist* gesture (all fingers clenched tightly).

        High score when no fingers are extended and average curl is
        near 1.0.
        """
        extended_count = sum(fingers)
        if extended_count > 1:
            return 0.0
        if extended_count == 1:
            # One finger barely sticking out — partial credit.
            base = 0.25
        else:
            base = 0.55

        # Higher curl → higher confidence.
        curl_bonus = min(0.45, curl * 0.5)
        return min(1.0, base + curl_bonus)

    # ── helper methods ────────────────────────────────────────────────

    @staticmethod
    def _finger_extension(
        landmarks: np.ndarray,
        handedness: str,
    ) -> List[bool]:
        """Determine which of the five fingers are extended.

        Args:
            landmarks: ``(21, 2|3)`` landmark array.
            handedness: ``"Left"`` or ``"Right"``.

        Returns:
            Five booleans ``[thumb, index, middle, ring, pinky]``.
            ``True`` means the finger is extended (up/out).
        """
        # Thumb uses x-axis comparison (mirrored for left/right hand).
        if handedness == "Right":
            thumb_up = float(landmarks[THUMB_TIP][0]) < float(landmarks[THUMB_IP][0])
        else:
            thumb_up = float(landmarks[THUMB_TIP][0]) > float(landmarks[THUMB_IP][0])

        # Other fingers: tip.y < pip.y means extended (MediaPipe uses
        # top-left origin, so lower y = higher on screen = extended).
        index_up = float(landmarks[INDEX_TIP][1]) < float(landmarks[INDEX_PIP][1])
        middle_up = float(landmarks[MIDDLE_TIP][1]) < float(landmarks[MIDDLE_PIP][1])
        ring_up = float(landmarks[RING_TIP][1]) < float(landmarks[RING_PIP][1])
        pinky_up = float(landmarks[PINKY_TIP][1]) < float(landmarks[PINKY_PIP][1])

        return [thumb_up, index_up, middle_up, ring_up, pinky_up]

    @staticmethod
    def _normalized_distance(
        landmarks: np.ndarray,
        idx_a: int,
        idx_b: int,
    ) -> float:
        """Euclidean distance between two landmarks (2-D or 3-D).

        The distance is computed in whatever coordinate space the
        landmarks use (typically normalised 0–1).

        Args:
            landmarks: ``(21, 2|3)`` array.
            idx_a: Index of the first landmark.
            idx_b: Index of the second landmark.

        Returns:
            Scalar distance (≥ 0).
        """
        a = landmarks[idx_a]
        b = landmarks[idx_b]
        return float(np.linalg.norm(a - b))

    @staticmethod
    def _average_curl(landmarks: np.ndarray) -> float:
        """Compute an average "curl" metric across all four non-thumb fingers.

        For each finger the curl is defined as::

            curl_i = clamp(pip.y − tip.y, 0, ∞) / reference_length

        where ``reference_length`` is the wrist→MIDDLE_MCP distance
        (a stable palm-size proxy).  A curl of 0 means the finger
        is fully straight (tip above pip); higher values mean more
        curled.

        Returns:
            Average curl in [0.0, 1.0] (clamped).
        """
        ref_len = float(
            np.linalg.norm(
                landmarks[WRIST][:2] - landmarks[MIDDLE_MCP][:2],
            )
        )
        if ref_len < 1e-6:
            return 0.0

        finger_pairs = [
            (INDEX_PIP, INDEX_TIP),
            (MIDDLE_PIP, MIDDLE_TIP),
            (RING_PIP, RING_TIP),
            (PINKY_PIP, PINKY_TIP),
        ]

        curls: List[float] = []
        for pip_idx, tip_idx in finger_pairs:
            pip_y = float(landmarks[pip_idx][1])
            tip_y = float(landmarks[tip_idx][1])
            curl_raw = max(0.0, pip_y - tip_y) / ref_len
            curls.append(min(1.0, curl_raw))

        return float(np.mean(curls))

    @staticmethod
    def _get_anchor(landmarks: np.ndarray) -> Tuple[float, float]:
        """Return the ``(x, y)`` position of the anchor landmark.

        The anchor is MIDDLE_MCP (index 9), which sits at the centre
        of the palm and provides the most stable cursor reference.
        """
        return (
            float(landmarks[ANCHOR_LANDMARK][0]),
            float(landmarks[ANCHOR_LANDMARK][1]),
        )
