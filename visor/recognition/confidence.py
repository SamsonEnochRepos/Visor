"""Temporal confidence and stability scoring for gesture recognition.

Wraps a sliding window of recent ``GestureResult`` classifications
and computes a *stability* metric — the proportion of frames in the
window that agree with the current gesture.  Downstream layers use
:py:meth:`should_act` as a gate: an action is only dispatched when
both the single-frame confidence *and* the temporal stability exceed
their respective thresholds.

Design rationale
────────────────
* The window-based approach smooths out single-frame misclassifications
  without introducing the latency of a state-machine debounce.
* ``dataclasses.replace()`` is used throughout to produce immutable
  copies of ``GestureResult`` — the scorer never mutates its input.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import replace
from typing import Deque

from visor.core.types import Gesture, GestureResult

logger = logging.getLogger("VISOR.recognition.confidence")


class ConfidenceScorer:
    """Rolling-window stability scorer for gesture classifications.

    Args:
        window_size: Number of recent frames to consider when
            computing stability.  Larger windows reduce noise but
            add latency to state transitions.  **Default 12** ≈ 400 ms
            at 30 FPS.
        min_confidence: Minimum single-frame classifier confidence
            required by :py:meth:`should_act`.
        min_stability: Minimum fraction of matching gestures in the
            window required by :py:meth:`should_act`.

    Usage::

        scorer = ConfidenceScorer()
        result = scorer.score(raw_result)
        if scorer.should_act(result):
            dispatch(result)
    """

    def __init__(
        self,
        window_size: int = 12,
        min_confidence: float = 0.55,
        min_stability: float = 0.45,
    ) -> None:
        self._window_size: int = window_size
        self._min_confidence: float = min_confidence
        self._min_stability: float = min_stability
        self._history: Deque[Gesture] = deque(maxlen=window_size)

    # ── public API ────────────────────────────────────────────────────

    def score(self, result: GestureResult) -> GestureResult:
        """Append the gesture to the history and return a copy of
        *result* with an updated ``stability`` field.

        Args:
            result: The ``GestureResult`` produced by the upstream
                classifier (static or motion).

        Returns:
            A **new** ``GestureResult`` whose ``stability`` field
            reflects the proportion of the sliding window that
            matches the current gesture.  All other fields are
            unchanged.
        """
        self._history.append(result.gesture)

        matching: int = sum(
            1 for g in self._history if g == result.gesture
        )
        stability: float = matching / len(self._history)

        return replace(result, stability=stability)

    def should_act(self, result: GestureResult) -> bool:
        """Decide whether the gesture is confident and stable enough
        to be acted upon.

        Both conditions must hold simultaneously:

        1. ``result.confidence ≥ min_confidence``
        2. ``result.stability  ≥ min_stability``

        Args:
            result: A ``GestureResult`` that has already been through
                :py:meth:`score` (i.e. its ``stability`` field is
                populated).

        Returns:
            ``True`` if both thresholds are met; ``False`` otherwise.
        """
        meets = (
            result.confidence >= self._min_confidence
            and result.stability >= self._min_stability
        )

        if meets:
            logger.debug(
                "Action gate OPEN: gesture=%s  conf=%.2f  stab=%.2f",
                result.gesture.name,
                result.confidence,
                result.stability,
            )

        return meets

    def reset(self) -> None:
        """Clear the gesture history.

        Call this when the hand is lost or when the system transitions
        to a qualitatively different mode (e.g. voice-command mode)
        so that stale history does not contaminate future stability
        calculations.
        """
        self._history.clear()
        logger.debug("Confidence history cleared.")

    # ── introspection (useful for debugging / overlay) ────────────────

    @property
    def window_size(self) -> int:
        """The configured sliding-window length."""
        return self._window_size

    @property
    def current_length(self) -> int:
        """Number of frames currently in the history buffer."""
        return len(self._history)
