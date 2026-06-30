"""Shared type definitions for the VISOR platform.

Defines the canonical data structures passed between layers:
  - Gesture / GestureResult  — recognition output
  - Intent / IntentResult    — intent-resolution output
  - LandmarkFrame            — perception-layer frame
"""

from __future__ import annotations

import dataclasses
import enum
import time
from typing import Any, Dict, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Gesture enum
# ---------------------------------------------------------------------------

class Gesture(enum.Enum):
    """Discrete hand-gesture classes recognised by the recognition layer."""

    NONE = "none"
    POINT = "point"
    PINCH = "pinch"
    GRAB = "grab"
    OPEN_PALM = "open_palm"
    FIST = "fist"
    SWIPE_LEFT = "swipe_left"
    SWIPE_RIGHT = "swipe_right"
    PUSH = "push"


# ---------------------------------------------------------------------------
# GestureResult
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class GestureResult:
    """Output of the gesture-recognition stage.

    Attributes:
        gesture: The classified gesture.
        confidence: Recognition confidence in [0.0, 1.0].
        stability: Temporal consistency over the last *N* frames in [0.0, 1.0].
        cursor_pos: Normalised screen position (x, y) in [0, 1] derived from
            a unified anchor landmark.
        landmarks: Optional 21×3 filtered landmark array (x, y, z).
        timestamp: Monotonic timestamp when the result was produced.
    """

    gesture: Gesture
    confidence: float          # 0.0–1.0
    stability: float           # 0.0–1.0, consistency over N frames
    cursor_pos: Tuple[float, float]  # Normalised (0–1) from unified anchor
    landmarks: Optional[np.ndarray] = None  # 21×3 filtered landmarks
    timestamp: float = dataclasses.field(default_factory=time.monotonic)

    # -- convenience factories ------------------------------------------------

    @staticmethod
    def idle() -> GestureResult:
        """Return a neutral *no-gesture* result."""
        return GestureResult(
            gesture=Gesture.NONE,
            confidence=0.0,
            stability=0.0,
            cursor_pos=(0.5, 0.5),
        )


# ---------------------------------------------------------------------------
# Intent enum
# ---------------------------------------------------------------------------

class Intent(enum.Enum):
    """High-level user intents derived from gestures + context."""

    IDLE = "idle"
    CURSOR_MOVE = "cursor_move"
    SELECT = "select"
    DRAG_START = "drag_start"
    DRAG_MOVE = "drag_move"
    DRAG_END = "drag_end"
    RESIZE = "resize"
    SCROLL = "scroll"
    CLOSE = "close"
    CONFIRM = "confirm"
    NAVIGATE_BACK = "navigate_back"
    NAVIGATE_FORWARD = "navigate_forward"
    APP_LAUNCH = "app_launch"
    VOICE_COMMAND = "voice_command"


# ---------------------------------------------------------------------------
# IntentResult
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class IntentResult:
    """Output of the intent-resolution stage.

    Attributes:
        intent: The resolved high-level intent.
        gesture: The underlying gesture result that triggered this intent.
        context: Arbitrary key/value metadata (e.g. voice transcript, app id).
        confidence: Overall confidence in [0.0, 1.0].
    """

    intent: Intent
    gesture: GestureResult
    context: Dict[str, Any] = dataclasses.field(default_factory=dict)
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# LandmarkFrame
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class LandmarkFrame:
    """A single frame of hand landmarks from the perception layer.

    Attributes:
        landmarks: 21×3 *filtered* landmark positions (x, y, z).
        handedness: ``"Left"`` or ``"Right"``.
        timestamp: Monotonic time in seconds when the frame was captured.
        raw_landmarks: 21×3 *pre-filter* landmark positions (x, y, z).
    """

    landmarks: np.ndarray      # 21×3 filtered landmarks
    handedness: str             # "Left" or "Right"
    timestamp: float            # Monotonic time in seconds
    raw_landmarks: np.ndarray   # 21×3 pre-filter landmarks
