"""One Euro Filter for adaptive landmark smoothing.

Implements the 1€ Filter from Casiez et al. 2012 (CHI '12) to reduce jitter
in hand-landmark positions while preserving responsiveness during fast
movements.  The filter adapts its cutoff frequency based on the rate of change
of the signal — slow movements get heavy smoothing, fast movements get minimal
lag.

References:
    Géry Casiez, Nicolas Roussel, Daniel Vogel. *1€ Filter: A Simple
    Speed-based Low-pass Filter for Noisy Input in Interactive Systems.*
    CHI 2012.  https://cristal.univ-lille.fr/~casiez/1euro/
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np

logger = logging.getLogger("VISOR.perception.landmark_filter")


# ---------------------------------------------------------------------------
# Low-pass filter (exponential smoothing)
# ---------------------------------------------------------------------------

class LowPassFilter:
    """Simple first-order exponential smoothing filter.

    Args:
        alpha: Smoothing factor in (0, 1].  Higher → less smoothing.

    Attributes:
        _alpha: Current smoothing factor.
        _prev: Previously filtered value (``None`` until first sample).
        _initialized: Whether the filter has received at least one sample.
    """

    def __init__(self, alpha: float = 1.0) -> None:
        self._alpha: float = self._clamp_alpha(alpha)
        self._prev: float = 0.0
        self._initialized: bool = False

    # -- public API -----------------------------------------------------------

    @property
    def prev(self) -> float:
        """Return the last filtered value."""
        return self._prev

    def filter(self, value: float, alpha: Optional[float] = None) -> float:
        """Apply exponential smoothing to *value*.

        Args:
            value: Raw input sample.
            alpha: Optional per-sample override for the smoothing factor.

        Returns:
            The filtered value.
        """
        if alpha is not None:
            self._alpha = self._clamp_alpha(alpha)

        if not self._initialized:
            self._prev = value
            self._initialized = True
            return value

        result = self._alpha * value + (1.0 - self._alpha) * self._prev
        self._prev = result
        return result

    def reset(self) -> None:
        """Clear internal state so the next sample acts as initialisation."""
        self._prev = 0.0
        self._initialized = False

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _clamp_alpha(alpha: float) -> float:
        """Clamp *alpha* into (0, 1]."""
        return max(0.0001, min(1.0, alpha))


# ---------------------------------------------------------------------------
# One Euro Filter
# ---------------------------------------------------------------------------

class OneEuroFilter:
    """Adaptive low-pass filter that balances jitter and lag.

    Args:
        freq: Expected sampling frequency (Hz).  Used as default when
            timestamps are not provided.
        min_cutoff: Minimum cutoff frequency (Hz).  Lower → more smoothing
            at rest.
        beta: Speed coefficient.  Higher → less lag during fast motion.
        d_cutoff: Cutoff frequency for the derivative filter (Hz).

    Raises:
        ValueError: If any parameter is non-positive.
    """

    def __init__(
        self,
        freq: float = 30.0,
        min_cutoff: float = 1.0,
        beta: float = 0.007,
        d_cutoff: float = 1.0,
    ) -> None:
        if freq <= 0 or min_cutoff <= 0 or beta < 0 or d_cutoff <= 0:
            raise ValueError(
                "freq and cutoff values must be positive; beta must be >= 0. "
                f"Got freq={freq}, min_cutoff={min_cutoff}, beta={beta}, d_cutoff={d_cutoff}"
            )

        self._freq: float = freq
        self._min_cutoff: float = min_cutoff
        self._beta: float = beta
        self._d_cutoff: float = d_cutoff

        self._x_filter = LowPassFilter()
        self._dx_filter = LowPassFilter()
        self._last_timestamp: Optional[float] = None

    # -- public API -----------------------------------------------------------

    def filter(self, value: float, timestamp: Optional[float] = None) -> float:
        """Filter a single scalar sample.

        Algorithm:
            1. Compute derivative of input.
            2. Low-pass filter the derivative (using ``d_cutoff``).
            3. Compute adaptive cutoff:
               ``cutoff = min_cutoff + beta * |filtered_derivative|``
            4. Low-pass filter the input using the adaptive cutoff.
            5. Return the filtered value.

        Args:
            value: Raw scalar input.
            timestamp: Optional monotonic timestamp (seconds).  If provided the
                sampling frequency is estimated from successive timestamps.

        Returns:
            The filtered scalar value.
        """
        # --- update frequency estimate from timestamps ---
        if timestamp is not None and self._last_timestamp is not None:
            dt = timestamp - self._last_timestamp
            if dt > 0:
                self._freq = 1.0 / dt
        self._last_timestamp = timestamp

        # --- step 1: derivative ---
        if not self._x_filter._initialized:
            dx = 0.0
        else:
            dx = (value - self._x_filter.prev) * self._freq

        # --- step 2: filter derivative ---
        alpha_d = self._compute_alpha(self._d_cutoff)
        filtered_dx = self._dx_filter.filter(dx, alpha=alpha_d)

        # --- step 3: adaptive cutoff ---
        cutoff = self._min_cutoff + self._beta * abs(filtered_dx)

        # --- step 4: filter input ---
        alpha = self._compute_alpha(cutoff)
        return self._x_filter.filter(value, alpha=alpha)

    def reset(self) -> None:
        """Clear all internal state."""
        self._x_filter.reset()
        self._dx_filter.reset()
        self._last_timestamp = None

    # -- helpers --------------------------------------------------------------

    def _compute_alpha(self, cutoff: float) -> float:
        """Compute smoothing factor from *cutoff* and current frequency.

        Formula::

            tau = 1 / (2 * pi * cutoff)
            te  = 1 / freq
            alpha = 1 / (1 + tau / te)

        Args:
            cutoff: Desired cutoff frequency (Hz).

        Returns:
            Smoothing factor alpha in (0, 1].
        """
        tau = 1.0 / (2.0 * math.pi * cutoff)
        te = 1.0 / self._freq
        return 1.0 / (1.0 + tau / te)


# ---------------------------------------------------------------------------
# Landmark filter (21 landmarks × 3 coords = 63 filters)
# ---------------------------------------------------------------------------

_NUM_LANDMARKS: int = 21
_NUM_COORDS: int = 3


class LandmarkFilter:
    """Applies independent One Euro Filters to each of the 21×3 landmark coords.

    This provides per-coordinate adaptive smoothing so that fast-moving
    landmarks (e.g. fingertips during a swipe) remain responsive while
    slower-moving ones (e.g. wrist) are heavily de-jittered.

    Args:
        freq: Expected sampling frequency (Hz).
        min_cutoff: Minimum cutoff frequency for all filters.
        beta: Speed coefficient for all filters.
        d_cutoff: Derivative cutoff for all filters.
    """

    def __init__(
        self,
        freq: float = 30.0,
        min_cutoff: float = 1.0,
        beta: float = 0.007,
        d_cutoff: float = 1.0,
    ) -> None:
        self._freq = freq
        self._min_cutoff = min_cutoff
        self._beta = beta
        self._d_cutoff = d_cutoff

        self._filters: list[list[OneEuroFilter]] = self._create_filters()
        logger.debug(
            "LandmarkFilter initialised: freq=%.1f Hz, min_cutoff=%.2f, beta=%.4f, d_cutoff=%.2f",
            freq,
            min_cutoff,
            beta,
            d_cutoff,
        )

    # -- public API -----------------------------------------------------------

    def filter_landmarks(self, landmarks: np.ndarray, timestamp: float) -> np.ndarray:
        """Filter a 21×3 landmark array.

        Args:
            landmarks: Raw landmark positions with shape ``(21, 3)``.
            timestamp: Monotonic timestamp of the frame (seconds).

        Returns:
            Filtered landmark positions as a new ``(21, 3)`` ``np.float64``
            array.

        Raises:
            ValueError: If *landmarks* does not have shape ``(21, 3)``.
        """
        if landmarks.shape != (_NUM_LANDMARKS, _NUM_COORDS):
            raise ValueError(
                f"Expected landmarks shape ({_NUM_LANDMARKS}, {_NUM_COORDS}), "
                f"got {landmarks.shape}"
            )

        filtered = np.empty_like(landmarks, dtype=np.float64)
        for i in range(_NUM_LANDMARKS):
            for j in range(_NUM_COORDS):
                filtered[i, j] = self._filters[i][j].filter(
                    float(landmarks[i, j]), timestamp=timestamp
                )
        return filtered

    def reset(self) -> None:
        """Reset all 63 internal filters to their initial state."""
        self._filters = self._create_filters()
        logger.debug("LandmarkFilter reset.")

    # -- helpers --------------------------------------------------------------

    def _create_filters(self) -> list[list[OneEuroFilter]]:
        """Allocate a fresh 21×3 grid of :class:`OneEuroFilter` instances."""
        return [
            [
                OneEuroFilter(
                    freq=self._freq,
                    min_cutoff=self._min_cutoff,
                    beta=self._beta,
                    d_cutoff=self._d_cutoff,
                )
                for _ in range(_NUM_COORDS)
            ]
            for _ in range(_NUM_LANDMARKS)
        ]
