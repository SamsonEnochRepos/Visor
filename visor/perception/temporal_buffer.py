"""Ring-buffer for temporal landmark frames with velocity/trajectory helpers.

Stores up to *max_frames* :class:`~visor.core.types.LandmarkFrame` instances
and exposes convenience methods for computing velocity, trajectory, and
displacement of individual landmarks over configurable sliding windows.  These
primitives are consumed downstream by gesture-recognition and intent-resolution
layers.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import List

import numpy as np

from visor.core.types import LandmarkFrame

logger = logging.getLogger("VISOR.perception.temporal_buffer")


class TemporalBuffer:
    """Fixed-capacity ring buffer of :class:`LandmarkFrame` instances.

    Args:
        max_frames: Maximum number of frames to retain.  Defaults to 60
            (≈ 2 seconds at 30 fps).

    Attributes:
        _buffer: Internal deque with a fixed ``maxlen``.
    """

    def __init__(self, max_frames: int = 60) -> None:
        if max_frames < 1:
            raise ValueError(f"max_frames must be >= 1, got {max_frames}")
        self._buffer: deque[LandmarkFrame] = deque(maxlen=max_frames)
        logger.debug("TemporalBuffer initialised with max_frames=%d", max_frames)

    # -- core operations ------------------------------------------------------

    def push(self, frame: LandmarkFrame) -> None:
        """Append *frame* to the buffer, evicting the oldest if full.

        Args:
            frame: A new landmark frame from the perception layer.
        """
        self._buffer.append(frame)

    def clear(self) -> None:
        """Remove all stored frames."""
        self._buffer.clear()
        logger.debug("TemporalBuffer cleared.")

    def __len__(self) -> int:
        """Return the number of frames currently stored."""
        return len(self._buffer)

    # -- windowed access ------------------------------------------------------

    def get_window(self, n_frames: int) -> List[LandmarkFrame]:
        """Return the most recent *n_frames* frames.

        If fewer than *n_frames* are available the returned list will be
        shorter.

        Args:
            n_frames: Number of recent frames to retrieve.

        Returns:
            A list of :class:`LandmarkFrame` in chronological order (oldest
            first).
        """
        if n_frames <= 0:
            return []
        # Slice from the right of the deque
        start = max(0, len(self._buffer) - n_frames)
        return list(self._buffer)[start:]

    # -- kinematics helpers ---------------------------------------------------

    def get_velocity(self, landmark_idx: int, window: int = 5) -> np.ndarray:
        """Compute the average velocity of a single landmark over *window* frames.

        Velocity is estimated as the mean of per-frame finite differences,
        scaled by the time between frames to give units of
        *normalised-coords / second*.

        Args:
            landmark_idx: Index of the landmark (0–20, MediaPipe convention).
            window: Number of recent frames to consider.

        Returns:
            A 3-element ``np.float64`` velocity vector ``(vx, vy, vz)``.
            Returns the zero vector if fewer than 2 frames are available.
        """
        frames = self.get_window(window)
        if len(frames) < 2:
            return np.zeros(3, dtype=np.float64)

        velocities: List[np.ndarray] = []
        for i in range(1, len(frames)):
            dt = frames[i].timestamp - frames[i - 1].timestamp
            if dt <= 0:
                continue
            dp = (
                frames[i].landmarks[landmark_idx].astype(np.float64)
                - frames[i - 1].landmarks[landmark_idx].astype(np.float64)
            )
            velocities.append(dp / dt)

        if not velocities:
            return np.zeros(3, dtype=np.float64)

        return np.mean(velocities, axis=0).astype(np.float64)

    def get_trajectory(self, landmark_idx: int, window: int = 30) -> np.ndarray:
        """Return the recent positions of a single landmark.

        Args:
            landmark_idx: Index of the landmark (0–20).
            window: Number of recent frames to include.

        Returns:
            An ``(N, 3)`` array of positions where ``N <= window``.
            Returns an empty ``(0, 3)`` array if the buffer is empty.
        """
        frames = self.get_window(window)
        if not frames:
            return np.empty((0, 3), dtype=np.float64)

        return np.array(
            [f.landmarks[landmark_idx] for f in frames],
            dtype=np.float64,
        )

    def get_displacement(self, landmark_idx: int, window: int = 15) -> np.ndarray:
        """Compute the end-to-start displacement vector of a landmark.

        Args:
            landmark_idx: Index of the landmark (0–20).
            window: Number of recent frames over which to measure displacement.

        Returns:
            A 3-element ``np.float64`` vector ``(dx, dy, dz)``.  Returns the
            zero vector if fewer than 2 frames are available.
        """
        frames = self.get_window(window)
        if len(frames) < 2:
            return np.zeros(3, dtype=np.float64)

        start = frames[0].landmarks[landmark_idx].astype(np.float64)
        end = frames[-1].landmarks[landmark_idx].astype(np.float64)
        return end - start

    # -- diagnostics ----------------------------------------------------------

    @property
    def fps(self) -> float:
        """Estimate the current frame rate from stored timestamps.

        Returns:
            Frames per second as a ``float``.  Returns ``0.0`` if fewer than
            two frames are stored or if the time span is zero.
        """
        if len(self._buffer) < 2:
            return 0.0

        dt = self._buffer[-1].timestamp - self._buffer[0].timestamp
        if dt <= 0:
            return 0.0

        return (len(self._buffer) - 1) / dt
