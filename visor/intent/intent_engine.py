"""
intent_engine.py — Core gesture-to-intent resolution for VISOR.

This is the central decoupling layer: raw ``GestureResult`` snapshots from
the recognition layer are combined with OS context to produce a high-level
``IntentResult`` consumed by the action layer.  The engine maintains minimal
state for gesture sequences that span multiple frames (e.g. pinch-hold →
drag, release → select).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from visor.core.types import (
    Gesture,
    GestureResult,
    Intent,
    IntentResult,
)

logger = logging.getLogger("VISOR.intent.engine")


class IntentEngine:
    """Stateful gesture → intent resolver.

    The engine runs once per frame.  It inspects the latest
    ``GestureResult``, compares it to the previous frame, and emits the
    appropriate ``IntentResult``.

    Pinch semantics:
        * A *quick* pinch-release (held < ``PINCH_HOLD_THRESHOLD_SEC``) is
          treated as a **SELECT** (click).
        * A *sustained* pinch (held ≥ threshold) enters **DRAG** mode, which
          continues until the pinch (or grab) is released.

    Example::

        engine = IntentEngine()
        intent = engine.resolve(gesture, context)
    """

    # ── Class Constants ─────────────────────────────────────────────────

    PINCH_HOLD_THRESHOLD_SEC: float = 0.25
    """Seconds a pinch must be held before it transitions to a drag."""

    # ── Lifecycle ───────────────────────────────────────────────────────

    def __init__(self) -> None:
        self._active_intent: Optional[Intent] = None
        self._drag_start_time: float = 0.0
        self._prev_gesture: Optional[GestureResult] = None
        self._pinch_start_time: float = 0.0
        self._pinch_was_held: bool = False

    # ── Public API ──────────────────────────────────────────────────────

    def resolve(
        self,
        gesture: GestureResult,
        context: Dict[str, Any],
    ) -> IntentResult:
        """Map a gesture snapshot + context to an actionable intent.

        Args:
            gesture: Current-frame gesture from the recognition layer.
            context: OS / window context from ``ContextManager``.

        Returns:
            The resolved ``IntentResult`` for this frame.
        """
        intent: Intent
        confidence: float = gesture.confidence

        # ── 1.  Active-drag continuation / termination ──────────────────
        if self._active_intent == Intent.DRAG_MOVE:
            if gesture.gesture in (Gesture.GRAB, Gesture.PINCH):
                # Still holding — continue the drag.
                intent = Intent.DRAG_MOVE
                self._prev_gesture = gesture
                return IntentResult(
                    intent=intent,
                    gesture=gesture,
                    context=context,
                    confidence=confidence,
                )
            # Released — end the drag.
            self._active_intent = None
            self._pinch_was_held = False
            intent = Intent.DRAG_END
            logger.debug("Drag ended (gesture released)")
            self._prev_gesture = gesture
            return IntentResult(
                intent=intent,
                gesture=gesture,
                context=context,
                confidence=confidence,
            )

        # ── 2.  Pinch release → SELECT  (prev was PINCH, current isn't) ─
        if (
            self._prev_gesture is not None
            and self._prev_gesture.gesture == Gesture.PINCH
            and gesture.gesture != Gesture.PINCH
            and not self._pinch_was_held
        ):
            self._pinch_was_held = False
            intent = Intent.SELECT
            logger.debug("Pinch released quickly → SELECT")
            self._prev_gesture = gesture
            return IntentResult(
                intent=intent,
                gesture=gesture,
                context=context,
                confidence=confidence,
            )

        # ── 3.  Gesture → intent mapping ────────────────────────────────
        intent = self._map_gesture(gesture)

        self._prev_gesture = gesture
        return IntentResult(
            intent=intent,
            gesture=gesture,
            context=context,
            confidence=confidence,
        )

    def reset(self) -> None:
        """Clear all internal state (e.g. on tracking loss)."""
        self._active_intent = None
        self._drag_start_time = 0.0
        self._prev_gesture = None
        self._pinch_start_time = 0.0
        self._pinch_was_held = False
        logger.debug("IntentEngine state reset")

    # ── Private Helpers ─────────────────────────────────────────────────

    def _map_gesture(self, gesture: GestureResult) -> Intent:
        """Resolve a single gesture to an intent (no prior-frame logic).

        This handles pinch-hold timing and immediate gesture mappings.
        """
        g = gesture.gesture

        if g == Gesture.POINT:
            return Intent.CURSOR_MOVE

        if g == Gesture.PINCH:
            return self._handle_pinch(gesture)

        if g == Gesture.GRAB:
            return self._start_drag(gesture)

        if g == Gesture.OPEN_PALM:
            return Intent.IDLE

        if g == Gesture.FIST:
            return Intent.CLOSE

        if g == Gesture.SWIPE_LEFT:
            return Intent.NAVIGATE_BACK

        if g == Gesture.SWIPE_RIGHT:
            return Intent.NAVIGATE_FORWARD

        if g == Gesture.PUSH:
            return Intent.CONFIRM

        # NONE or any unknown gesture
        return Intent.IDLE

    def _handle_pinch(self, gesture: GestureResult) -> Intent:
        """Pinch state machine: first frame → start timer; held → drag."""
        prev_was_pinch = (
            self._prev_gesture is not None
            and self._prev_gesture.gesture == Gesture.PINCH
        )

        if not prev_was_pinch:
            # First frame of a new pinch — start the clock.
            self._pinch_start_time = gesture.timestamp
            self._pinch_was_held = False
            logger.debug("Pinch started at %.3f", self._pinch_start_time)
            return Intent.CURSOR_MOVE  # not yet committed to drag or select

        # Pinch is ongoing — check hold duration.
        hold_duration = gesture.timestamp - self._pinch_start_time
        if hold_duration >= self.PINCH_HOLD_THRESHOLD_SEC:
            self._pinch_was_held = True
            return self._start_drag(gesture)

        # Still within the hold threshold — keep moving the cursor.
        return Intent.CURSOR_MOVE

    def _start_drag(self, gesture: GestureResult) -> Intent:
        """Transition into drag mode."""
        if self._active_intent != Intent.DRAG_MOVE:
            self._active_intent = Intent.DRAG_MOVE
            self._drag_start_time = gesture.timestamp
            logger.debug(
                "Drag started (gesture=%s, t=%.3f)",
                gesture.gesture.value,
                self._drag_start_time,
            )
            return Intent.DRAG_START
        return Intent.DRAG_MOVE
