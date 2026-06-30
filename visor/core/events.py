"""Thread-safe publish/subscribe event bus for decoupling VISOR components.

Usage::

    from visor.core.events import get_event_bus, EVENT_GESTURE_DETECTED

    bus = get_event_bus()
    bus.subscribe(EVENT_GESTURE_DETECTED, my_handler)
    bus.publish(EVENT_GESTURE_DETECTED, gesture_result)
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any, Callable, Dict, List

logger = logging.getLogger("VISOR.events")


class EventBus:
    """Thread-safe publish/subscribe event bus for decoupling VISOR components.

    Subscribers are invoked synchronously in the publishing thread.  Heavy
    handlers should offload work to a queue or thread pool.

    Attributes:
        _subscribers: Mapping of event type strings to ordered callback lists.
        _lock: Guards concurrent access to ``_subscribers``.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable[[Any], None]]] = defaultdict(list)
        self._lock = threading.Lock()

    # -- public API -----------------------------------------------------------

    def subscribe(self, event_type: str, callback: Callable[[Any], None]) -> None:
        """Register *callback* to be called whenever *event_type* is published.

        Args:
            event_type: Dot-separated event name (e.g. ``"gesture.detected"``).
            callback: Callable accepting a single *data* argument.
        """
        with self._lock:
            self._subscribers[event_type].append(callback)
            logger.debug("Subscribed to '%s': %s", event_type, callback.__name__)

    def unsubscribe(self, event_type: str, callback: Callable[[Any], None]) -> None:
        """Remove a previously registered *callback* for *event_type*.

        Silently ignores the call if the callback was never registered.

        Args:
            event_type: Dot-separated event name.
            callback: The exact callable reference to remove.
        """
        with self._lock:
            try:
                self._subscribers[event_type].remove(callback)
                logger.debug("Unsubscribed from '%s': %s", event_type, callback.__name__)
            except ValueError:
                pass

    def publish(self, event_type: str, data: Any = None) -> None:
        """Publish *data* to all subscribers of *event_type*.

        Exceptions in individual handlers are caught and logged so that one
        failing handler does not prevent the remaining handlers from executing.

        Args:
            event_type: Dot-separated event name.
            data: Arbitrary payload forwarded to each subscriber.
        """
        with self._lock:
            subscribers = list(self._subscribers.get(event_type, []))

        for cb in subscribers:
            try:
                cb(data)
            except Exception as exc:
                logger.error(
                    "Event handler error for '%s' in %s: %s",
                    event_type,
                    cb.__name__,
                    exc,
                    exc_info=True,
                )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_bus = EventBus()


def get_event_bus() -> EventBus:
    """Return the process-wide singleton :class:`EventBus` instance."""
    return _bus


# ---------------------------------------------------------------------------
# Event type constants
# ---------------------------------------------------------------------------

EVENT_GESTURE_DETECTED: str = "gesture.detected"
EVENT_INTENT_RESOLVED: str = "intent.resolved"
EVENT_ACTION_EXECUTED: str = "action.executed"
EVENT_VOICE_COMMAND: str = "voice.command"
EVENT_HAND_LOST: str = "hand.lost"
EVENT_HAND_FOUND: str = "hand.found"
