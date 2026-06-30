"""
camera_provider.py — Abstract camera input for VISOR.

Provides a clean abstraction over camera hardware so the perception
pipeline doesn't care whether frames come from a webcam, video file,
or future AR glasses feed.
"""

import abc
import logging
import time
from typing import Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("VISOR.input.camera")


class CameraProvider(abc.ABC):
    """Abstract camera input — webcam today, AR glasses tomorrow."""

    @abc.abstractmethod
    def start(self) -> bool:
        """Open the camera. Returns True on success."""
        ...

    @abc.abstractmethod
    def read_frame(self) -> Optional[Tuple[np.ndarray, float]]:
        """Read one RGB frame. Returns (rgb_array, timestamp) or None."""
        ...

    @abc.abstractmethod
    def stop(self) -> None:
        """Release camera resources."""
        ...

    @abc.abstractmethod
    def get_resolution(self) -> Tuple[int, int]:
        """Return (width, height) of output frames."""
        ...


class WebcamProvider(CameraProvider):
    """OpenCV webcam implementation with DirectShow fallback."""

    def __init__(self, camera_index: int = 0,
                 width: int = 640, height: int = 480,
                 target_fps: int = 60) -> None:
        self._cam_idx = camera_index
        self._width = width
        self._height = height
        self._target_fps = target_fps
        self._cap: Optional[cv2.VideoCapture] = None

    def start(self) -> bool:
        """Open webcam with DirectShow backend, fallback to default."""
        logger.info("Opening camera %d (%dx%d @ %dfps)",
                    self._cam_idx, self._width, self._height, self._target_fps)

        # Try DirectShow first (faster on Windows)
        self._cap = cv2.VideoCapture(self._cam_idx, cv2.CAP_DSHOW)
        if not self._cap.isOpened():
            logger.warning("DirectShow failed, trying default backend")
            self._cap = cv2.VideoCapture(self._cam_idx)

        if not self._cap.isOpened():
            logger.error("Cannot open camera index %d", self._cam_idx)
            return False

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._cap.set(cv2.CAP_PROP_FPS, self._target_fps)

        # Verify with a test frame
        ret, frame = self._cap.read()
        if not ret or frame is None:
            logger.error("Test frame read failed — camera may be busy")
            self._cap.release()
            self._cap = None
            return False

        actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        logger.info("Camera opened: %dx%d (requested %dx%d)",
                    actual_w, actual_h, self._width, self._height)
        return True

    def read_frame(self) -> Optional[Tuple[np.ndarray, float]]:
        """Read one frame, mirror it, resize, convert to RGB."""
        if self._cap is None or not self._cap.isOpened():
            return None

        ret, frame = self._cap.read()
        if not ret or frame is None:
            return None

        timestamp = time.monotonic()

        # Mirror horizontally for natural interaction
        frame = cv2.flip(frame, 1)

        # Resize to target resolution
        h, w = frame.shape[:2]
        if w != self._width or h != self._height:
            frame = cv2.resize(frame, (self._width, self._height))

        # Convert BGR → RGB for MediaPipe
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return rgb, timestamp

    def stop(self) -> None:
        """Release the camera."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("Camera released")

    def get_resolution(self) -> Tuple[int, int]:
        """Return target output resolution."""
        return self._width, self._height


class VideoFileProvider(CameraProvider):
    """Replay from a video file — for testing and debugging."""

    def __init__(self, video_path: str,
                 width: int = 640, height: int = 480,
                 loop: bool = True) -> None:
        self._path = video_path
        self._width = width
        self._height = height
        self._loop = loop
        self._cap: Optional[cv2.VideoCapture] = None

    def start(self) -> bool:
        self._cap = cv2.VideoCapture(self._path)
        if not self._cap.isOpened():
            logger.error("Cannot open video file: %s", self._path)
            return False
        logger.info("Video file opened: %s", self._path)
        return True

    def read_frame(self) -> Optional[Tuple[np.ndarray, float]]:
        if self._cap is None:
            return None

        ret, frame = self._cap.read()
        if not ret:
            if self._loop:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = self._cap.read()
                if not ret:
                    return None
            else:
                return None

        timestamp = time.monotonic()
        frame = cv2.flip(frame, 1)
        if frame.shape[1] != self._width or frame.shape[0] != self._height:
            frame = cv2.resize(frame, (self._width, self._height))
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return rgb, timestamp

    def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def get_resolution(self) -> Tuple[int, int]:
        return self._width, self._height
