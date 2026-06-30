"""
gesture_engine.py - MediaPipe hand tracking + physics-based gesture state machine.

Runs in its own thread at ~30 FPS. Classifies gestures using velocity,
dwell time, and acceleration rather than static shape lookups.
Uses the new MediaPipe Tasks API (HandLandmarker).
"""

import os
import time
import math
import logging
import threading
import enum
import queue
from typing import Optional, Tuple, Dict, List, Any
from collections import deque

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

from config import Config
from mouse_controller import MouseController

logger = logging.getLogger("VISOR.gesture")


class GestureState(enum.Enum):
    """All states in the gesture state machine."""
    IDLE = "idle"
    POINTING = "pointing"
    HOVERING = "hovering"
    CLICKING = "clicking"
    DOUBLE_CLICKING = "double_clicking"
    DRAGGING = "dragging"
    FLINGING = "flinging"
    PINCHING = "pinching"
    QUICK_SELECT = "quick_select"
    GRABBING = "grabbing"
    DRAG_MOVING = "drag_moving"
    SPREADING = "spreading"
    CLOSING_PINCH = "closing_pinch"
    PALM_OPEN = "palm_open"
    PALM_HOLD = "palm_hold"
    PALM_DRAG = "palm_drag"
    FIST_CLOSE = "fist_close"
    PALM_FLING = "palm_fling"
    TWO_HAND = "two_hand"
    EXPAND = "expand"
    COMPRESS = "compress"
    ROTATE = "rotate"
    PUSH = "push"
    CLAP = "clap"


# Landmark indices
WRIST = 0
THUMB_TIP = 4; THUMB_IP = 3
INDEX_TIP = 8; INDEX_PIP = 6
MIDDLE_MCP = 9  # FIX 3: palm center — more stable for cursor
MIDDLE_TIP = 12; MIDDLE_PIP = 10
RING_TIP = 16; RING_PIP = 14
PINKY_TIP = 20; PINKY_PIP = 18

TIPS = [THUMB_TIP, INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP]
PIPS = [THUMB_IP, INDEX_PIP, MIDDLE_PIP, RING_PIP, PINKY_PIP]


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _lm_xy(landmarks: list, idx: int) -> Tuple[float, float]:
    """Extract normalized (x, y) from a landmark list (Tasks API format)."""
    lm = landmarks[idx]
    return (lm.x, lm.y)


class HandTracker:
    """Tracks velocity, acceleration, and dwell for a single hand."""

    def __init__(self) -> None:
        self.prev_wrist: Optional[Tuple[float, float]] = None
        self.prev_time: float = 0.0
        self.velocity: np.ndarray = np.array([0.0, 0.0])
        self.smooth_velocity: np.ndarray = np.array([0.0, 0.0])
        self.speed: float = 0.0
        self.prev_speed: float = 0.0
        self.acceleration: float = 0.0
        self.dwell_start: float = 0.0
        self.dwell_position: Optional[Tuple[float, float]] = None
        self.prev_pinch_dist: float = 1.0
        self.pinch_velocity: float = 0.0

    def update(self, wrist: Tuple[float, float], now: float, cfg: Config) -> None:
        dt = now - self.prev_time if self.prev_time > 0 else 1.0 / 30.0
        dt = max(dt, 0.001)

        if self.prev_wrist is not None:
            raw_v = np.array([(wrist[0] - self.prev_wrist[0]) / dt,
                              (wrist[1] - self.prev_wrist[1]) / dt])
            alpha = cfg["VELOCITY_SMOOTHING"]
            self.smooth_velocity = self.smooth_velocity * alpha + raw_v * (1.0 - alpha)
        else:
            self.smooth_velocity = np.array([0.0, 0.0])

        self.prev_speed = self.speed
        self.speed = float(np.linalg.norm(self.smooth_velocity))
        self.acceleration = (self.speed - self.prev_speed) / dt
        self.velocity = self.smooth_velocity

        # Dwell tracking
        dwell_thresh = 0.02
        if self.prev_wrist is not None and _dist(wrist, self.prev_wrist) < dwell_thresh * dt:
            if self.dwell_position is None:
                self.dwell_position = wrist
                self.dwell_start = now
        else:
            self.dwell_position = None
            self.dwell_start = now

        self.prev_wrist = wrist
        self.prev_time = now

    @property
    def dwell_ms(self) -> float:
        if self.dwell_position is None:
            return 0.0
        return (time.time() - self.dwell_start) * 1000.0

    def update_pinch(self, pinch_dist: float, dt: float) -> None:
        self.pinch_velocity = (pinch_dist - self.prev_pinch_dist) / max(dt, 0.001)
        self.prev_pinch_dist = pinch_dist


# FIX 4: Finger ID constants for is_finger_up()
FINGER_THUMB = 0
FINGER_INDEX = 1
FINGER_MIDDLE = 2
FINGER_RING = 3
FINGER_PINKY = 4

_FINGER_TIP_PIP = {
    FINGER_INDEX: (INDEX_TIP, INDEX_PIP),
    FINGER_MIDDLE: (MIDDLE_TIP, MIDDLE_PIP),
    FINGER_RING: (RING_TIP, RING_PIP),
    FINGER_PINKY: (PINKY_TIP, PINKY_PIP),
}


def is_finger_up(landmarks: list, finger_id: int, handedness: str = "Right") -> bool:
    """FIX 4: Check if a single finger is extended.

    For thumb: uses x-axis comparison based on handedness.
    For others: tip_y < pip_y means extended.
    """
    if finger_id == FINGER_THUMB:
        thumb_tip = _lm_xy(landmarks, THUMB_TIP)
        thumb_ip = _lm_xy(landmarks, THUMB_IP)
        if handedness == "Right":
            return thumb_tip[0] < thumb_ip[0]
        else:
            return thumb_tip[0] > thumb_ip[0]
    tip_idx, pip_idx = _FINGER_TIP_PIP[finger_id]
    tip = _lm_xy(landmarks, tip_idx)
    pip_ = _lm_xy(landmarks, pip_idx)
    return tip[1] < pip_[1]


def _fingers_extended(landmarks: list, handedness: str) -> List[bool]:
    """Return [thumb, index, middle, ring, pinky] extension booleans."""
    return [is_finger_up(landmarks, f, handedness) for f in range(5)]


def _detect_hand_shape(landmarks: list, handedness: str) -> str:
    """Classify basic hand shape from landmarks."""
    ext = _fingers_extended(landmarks, handedness)
    thumb, index, middle, ring, pinky = ext

    if all(ext):
        return "palm"
    if not any(ext):
        return "fist"
    if index and not middle and not ring and not pinky:
        return "point"
    if index and middle and not ring and not pinky:
        return "peace"
    return "other"


class GestureStateMachine:
    """Physics-based gesture state machine with hysteresis."""

    def __init__(self, mouse: MouseController, shared_state: Dict[str, Any]) -> None:
        self._mouse = mouse
        self._state = GestureState.IDLE
        self._shared = shared_state
        self._cfg = Config.get()
        self._trackers: Dict[str, HandTracker] = {"Left": HandTracker(), "Right": HandTracker()}
        self._hysteresis: Dict[str, int] = {}
        self._pinch_start_time: float = 0.0
        self._last_jab_time: float = 0.0
        self._jab_count: int = 0
        self._prev_inter_hand_dist: float = 0.0
        self._prev_inter_hand_angle: float = 0.0
        self._palm_drag_origin: Optional[Tuple[float, float]] = None
        self._prev_two_hand_center: Optional[Tuple[float, float]] = None

        # FIX 4: Gesture confirmation buffer
        self._gesture_buffer: deque = deque(maxlen=self._cfg["GESTURE_CONFIRM_FRAMES"])

        # FIX 4: Per-gesture cooldown timers
        self._last_triggered: Dict[str, float] = {
            'click': 0, 'double_click': 0, 'drag': 0, 'swipe': 0,
            'fling': 0, 'pinch': 0, 'palm_fling': 0, 'fist_close': 0,
            'right_click': 0, 'quick_select': 0,
        }

        # FIX 4: Wrist velocity tracking for flick detection
        self._recent_wrist_positions: deque = deque(maxlen=5)

        # FIX 6: Window grab state
        self._grabbed_window = None
        self._win_start_x: int = 0
        self._win_start_y: int = 0
        self._drag_start_mouse: Optional[Tuple[int, int]] = None

    @property
    def state(self) -> GestureState:
        return self._state

    def _check_hysteresis(self, condition_key: str, condition: bool) -> bool:
        """FIX 4: Require condition true for GESTURE_CONFIRM_FRAMES consecutive frames."""
        required = self._cfg["GESTURE_CONFIRM_FRAMES"]
        if condition:
            self._hysteresis[condition_key] = self._hysteresis.get(condition_key, 0) + 1
            return self._hysteresis[condition_key] >= required
        else:
            self._hysteresis[condition_key] = 0
            return False

    def _cooldown_ok(self, gesture_key: str) -> bool:
        """FIX 4: Check per-gesture cooldown timer."""
        cooldown_map = {
            'click': self._cfg["CLICK_COOLDOWN_SEC"],
            'double_click': self._cfg["CLICK_COOLDOWN_SEC"],
            'right_click': self._cfg["CLICK_COOLDOWN_SEC"],
            'quick_select': self._cfg["CLICK_COOLDOWN_SEC"],
            'swipe': self._cfg["SWIPE_COOLDOWN_SEC"],
            'fling': self._cfg["FLING_COOLDOWN_SEC"],
            'palm_fling': self._cfg["FLING_COOLDOWN_SEC"],
            'fist_close': self._cfg["CLICK_COOLDOWN_SEC"],
        }
        cooldown = cooldown_map.get(gesture_key, 0.3)
        now = time.time()
        if now - self._last_triggered.get(gesture_key, 0) < cooldown:
            return False
        self._last_triggered[gesture_key] = now
        return True

    def _get_wrist_flick_velocity(self) -> float:
        """FIX 4: Calculate wrist velocity from recent position history."""
        if len(self._recent_wrist_positions) < 2:
            return 0.0
        first = self._recent_wrist_positions[0]
        last = self._recent_wrist_positions[-1]
        return _dist(first, last)

    def _transition(self, new_state: GestureState) -> None:
        if new_state != self._state:
            logger.info("Gesture: %s -> %s", self._state.value, new_state.value)  # FIX: log confirmed gestures
            old = self._state
            self._state = new_state
            self._shared["gesture_state"] = new_state.value
            # Cleanup on leaving drag states
            if old in (GestureState.DRAGGING, GestureState.DRAG_MOVING) and \
               new_state not in (GestureState.DRAGGING, GestureState.DRAG_MOVING):
                self._mouse.mouse_up()
            if old == GestureState.PALM_DRAG and new_state != GestureState.PALM_DRAG:
                self._palm_drag_origin = None
                self._grabbed_window = None  # FIX 6: release window

    def process_frame(self, result, frame_time: float) -> None:
        """Process one MediaPipe HandLandmarkerResult through the state machine."""
        now = time.time()

        if not result.hand_landmarks:
            self._transition(GestureState.IDLE)
            return

        hands = result.hand_landmarks
        hand_labels = []
        if result.handedness:
            for h in result.handedness:
                hand_labels.append(h[0].category_name)
        else:
            hand_labels = ["Right"] * len(hands)

        num_hands = len(hands)

        # Update trackers
        for i, (lms, label) in enumerate(zip(hands, hand_labels)):
            wrist = _lm_xy(lms, WRIST)
            tracker = self._trackers.setdefault(label, HandTracker())
            tracker.update(wrist, now, self._cfg)
            # Update pinch distance tracking
            pinch_d = _dist(_lm_xy(lms, THUMB_TIP), _lm_xy(lms, INDEX_TIP))
            tracker.update_pinch(pinch_d, frame_time)

        # FIX 4: Track wrist positions for flick velocity
        wrist0 = _lm_xy(hands[0], WRIST)
        self._recent_wrist_positions.append(wrist0)

        # FIX 4: Gesture priority — if dragging, only allow drag-related transitions
        if self._state in (GestureState.DRAGGING, GestureState.DRAG_MOVING, GestureState.PALM_DRAG):
            # Stay in drag mode — only process the current drag handler
            if num_hands >= 2:
                pass  # don't process two-hand while dragging
            else:
                lms = hands[0]
                label = hand_labels[0]
                tracker = self._trackers[label]
                cursor_pos = _lm_xy(lms, MIDDLE_MCP)
                wrist_pos = _lm_xy(lms, WRIST)
                pinch_dist = _dist(_lm_xy(lms, THUMB_TIP), _lm_xy(lms, INDEX_TIP))
                threshold = self._cfg["PINCH_THRESHOLD"]
                shape = _detect_hand_shape(lms, label)

                if self._state == GestureState.PALM_DRAG:
                    self._process_palm(tracker, wrist_pos, now)
                elif pinch_dist >= threshold and self._state in (GestureState.DRAGGING, GestureState.DRAG_MOVING):
                    # Released pinch — end drag
                    self._transition(GestureState.IDLE)
                else:
                    self._mouse.move_to_normalized(cursor_pos[0], cursor_pos[1])
            return

        # --- TWO HAND GESTURES ---
        if num_hands >= 2:
            self._process_two_hands(hands, hand_labels, now)
            return

        # --- SINGLE HAND GESTURES ---
        lms = hands[0]
        label = hand_labels[0]
        tracker = self._trackers[label]
        shape = _detect_hand_shape(lms, label)
        cursor_pos = _lm_xy(lms, MIDDLE_MCP)  # FIX 3: palm center for cursor
        index_pos = _lm_xy(lms, INDEX_TIP)  # kept for pinch calc
        wrist_pos = _lm_xy(lms, WRIST)
        pinch_dist = _dist(_lm_xy(lms, THUMB_TIP), _lm_xy(lms, INDEX_TIP))
        threshold = self._cfg["PINCH_THRESHOLD"]

        # --- PALM ---
        if self._check_hysteresis("palm", shape == "palm"):
            self._process_palm(tracker, wrist_pos, now)
            return

        # --- FIST (from palm states) ---
        if shape == "fist" and self._state in (GestureState.PALM_OPEN, GestureState.PALM_HOLD,
                                                GestureState.PALM_DRAG):
            if self._check_hysteresis("fist_close", True) and self._cooldown_ok('fist_close'):
                if tracker.speed > self._cfg["FIST_CLOSE_VELOCITY"]:
                    self._transition(GestureState.FIST_CLOSE)
                    self._mouse.hotkey("alt", "F4")
                else:
                    self._transition(GestureState.FIST_CLOSE)
                    self._mouse.hotkey("win", "down")
                self._transition(GestureState.IDLE)
                return
        else:
            self._hysteresis.pop("fist_close", None)

        # --- PINCH ---
        if self._check_hysteresis("pinch", pinch_dist < threshold):
            self._process_pinch(tracker, cursor_pos, wrist_pos, now)
            return

        # --- Release from pinch states ---
        if self._state in (GestureState.PINCHING, GestureState.GRABBING, GestureState.DRAG_MOVING):
            if pinch_dist >= threshold:
                if self._state == GestureState.PINCHING:
                    elapsed = (now - self._pinch_start_time) * 1000
                    if elapsed < self._cfg["PINCH_QUICK_RELEASE_MS"]:
                        self._transition(GestureState.QUICK_SELECT)
                        self._mouse.click()
                self._transition(GestureState.IDLE)
                return

        # --- SPREADING / CLOSING PINCH (single hand zoom) ---
        if shape == "other":
            pv = tracker.pinch_velocity
            spread_thresh = self._cfg["SPREAD_VELOCITY_THRESHOLD"]
            if self._check_hysteresis("spread", pv > spread_thresh):
                self._transition(GestureState.SPREADING)
                self._mouse.hotkey("ctrl", "=")
                return
            if self._check_hysteresis("close_pinch", pv < -spread_thresh):
                self._transition(GestureState.CLOSING_PINCH)
                self._mouse.hotkey("ctrl", "-")
                return

        # --- POINT ---
        if self._check_hysteresis("point", shape == "point"):
            self._process_point(tracker, cursor_pos, wrist_pos, now)
            return

        # --- PEACE (right click) ---
        if self._check_hysteresis("peace", shape == "peace"):
            if self._cooldown_ok('right_click'):
                self._mouse.right_click()
            self._transition(GestureState.IDLE)
            return

        # --- FIST standalone ---
        if self._check_hysteresis("fist_standalone", shape == "fist"):
            self._transition(GestureState.IDLE)
            return

        # Default: move cursor if pointing-ish
        if shape == "point":
            self._mouse.move_to_normalized(cursor_pos[0], cursor_pos[1])

    def _process_point(self, tracker: HandTracker, cursor_pos: Tuple[float, float],
                       wrist_pos: Tuple[float, float], now: float) -> None:
        """Handle pointing state transitions based on velocity and dwell."""
        speed = tracker.speed
        dwell = tracker.dwell_ms
        cfg = self._cfg

        # Move cursor always when pointing
        self._mouse.move_to_normalized(cursor_pos[0], cursor_pos[1])

        # FLINGING - very fast release
        if speed > cfg["VELOCITY_FLING"]:
            if self._check_hysteresis("fling", True):
                self._transition(GestureState.FLINGING)
                # Determine direction from velocity
                vx, vy = tracker.velocity
                if abs(vx) > abs(vy):
                    if vx > 0:
                        self._mouse.hotkey("alt", "right")
                    else:
                        self._mouse.hotkey("alt", "left")
                self._transition(GestureState.IDLE)
                return
        else:
            self._hysteresis.pop("fling", None)

        # JAB detection - velocity spike then stop
        if speed > cfg["JAB_VELOCITY_THRESHOLD"] and self._state != GestureState.DRAGGING:
            self._jab_count += 1
            self._last_jab_time = now
        elif speed < cfg["JAB_STOP_THRESHOLD"] and self._jab_count > 0:
            elapsed_since_jab = (now - self._last_jab_time) * 1000
            if elapsed_since_jab < cfg["DOUBLE_JAB_WINDOW_MS"] and self._jab_count >= 2:
                self._transition(GestureState.DOUBLE_CLICKING)
                self._mouse.double_click()
                self._jab_count = 0
                self._transition(GestureState.POINTING)
                return
            elif elapsed_since_jab < cfg["CLICK_COOLDOWN_MS"] and self._jab_count == 1:
                # Wait to see if second jab comes
                pass
            elif self._jab_count == 1 and elapsed_since_jab >= cfg["DOUBLE_JAB_WINDOW_MS"]:
                self._transition(GestureState.CLICKING)
                self._mouse.click()
                self._jab_count = 0
                self._transition(GestureState.POINTING)
                return

        # DWELL -> HOVER or DRAG
        if dwell > cfg["DWELL_TIME_MS"]:
            if self._state == GestureState.HOVERING and speed > cfg["VELOCITY_LOW"]:
                # Dwell then movement = drag
                self._transition(GestureState.DRAGGING)
                self._mouse.mouse_down()
                return
            elif self._state != GestureState.DRAGGING:
                self._transition(GestureState.HOVERING)
                return

        # Continue drag if already dragging
        if self._state == GestureState.DRAGGING:
            return

        self._transition(GestureState.POINTING)

    def _process_pinch(self, tracker: HandTracker, cursor_pos: Tuple[float, float],
                       wrist_pos: Tuple[float, float], now: float) -> None:
        """Handle pinch state transitions."""
        cfg = self._cfg

        if self._state not in (GestureState.PINCHING, GestureState.GRABBING,
                                GestureState.DRAG_MOVING):
            self._transition(GestureState.PINCHING)
            self._pinch_start_time = now
            return

        hold_duration = (now - self._pinch_start_time) * 1000

        if self._state == GestureState.PINCHING:
            if hold_duration > cfg["PINCH_QUICK_RELEASE_MS"]:
                self._transition(GestureState.GRABBING)
                self._mouse.mouse_down()
                return

        if self._state == GestureState.GRABBING:
            if tracker.speed > cfg["VELOCITY_LOW"]:
                self._transition(GestureState.DRAG_MOVING)

        if self._state in (GestureState.GRABBING, GestureState.DRAG_MOVING):
            self._mouse.move_to_normalized(cursor_pos[0], cursor_pos[1])

    def _process_palm(self, tracker: HandTracker, wrist_pos: Tuple[float, float],
                      now: float) -> None:
        """Handle palm states - hold, drag, fling. FIX 6: window grabbing."""
        cfg = self._cfg
        speed = tracker.speed
        dwell = tracker.dwell_ms

        # Palm fling
        if speed > cfg["PALM_FLING_VELOCITY"]:
            if self._check_hysteresis("palm_fling", True) and self._cooldown_ok('palm_fling'):
                self._transition(GestureState.PALM_FLING)
                vx = tracker.velocity[0]
                if vx > 0:
                    self._mouse.hotkey("alt", "F4")
                else:
                    self._mouse.hotkey("win", "d")
                self._transition(GestureState.IDLE)
                return
        else:
            self._hysteresis.pop("palm_fling", None)

        if self._state == GestureState.PALM_DRAG:
            # FIX 6: Continue dragging window via pygetwindow
            if self._grabbed_window is not None and self._drag_start_mouse is not None:
                mouse_x, mouse_y = self._mouse.get_position()
                delta_x = mouse_x - self._drag_start_mouse[0]
                delta_y = mouse_y - self._drag_start_mouse[1]
                try:
                    self._grabbed_window.moveTo(
                        self._win_start_x + delta_x,
                        self._win_start_y + delta_y
                    )
                except Exception:
                    # FIX 6 fallback: use raw win32 move
                    if self._palm_drag_origin is not None:
                        dx = int((wrist_pos[0] - self._palm_drag_origin[0]) * 1920)
                        dy = int((wrist_pos[1] - self._palm_drag_origin[1]) * 1080)
                        self._mouse.move_foreground_window(dx, dy)
            elif self._palm_drag_origin is not None:
                # Fallback: no pygetwindow
                dx = int((wrist_pos[0] - self._palm_drag_origin[0]) * 1920)
                dy = int((wrist_pos[1] - self._palm_drag_origin[1]) * 1080)
                self._mouse.move_foreground_window(dx, dy)
            self._palm_drag_origin = wrist_pos
            return

        if self._state == GestureState.PALM_HOLD:
            if speed > cfg["VELOCITY_LOW"]:
                self._transition(GestureState.PALM_DRAG)
                self._palm_drag_origin = wrist_pos
                # FIX 6: Grab the active window
                self._drag_start_mouse = self._mouse.get_position()
                try:
                    import pygetwindow as gw
                    active_win = gw.getActiveWindow()
                    if active_win:
                        self._grabbed_window = active_win
                        self._win_start_x = active_win.left
                        self._win_start_y = active_win.top
                        logger.info("FIX 6: Grabbed window '%s' at (%d,%d)",
                                    active_win.title, active_win.left, active_win.top)
                except ImportError:
                    logger.warning("pygetwindow not installed, using win32 fallback for drag")
                    self._grabbed_window = None
                except Exception as exc:
                    logger.warning("Could not grab window: %s, using fallback", exc)
                    self._grabbed_window = None
                return

        if dwell > cfg["DWELL_TIME_MS"]:
            self._transition(GestureState.PALM_HOLD)
        else:
            self._transition(GestureState.PALM_OPEN)

    def _process_two_hands(self, hands: list, labels: list, now: float) -> None:
        """Handle two-hand gestures: expand, compress, rotate, push, clap."""
        cfg = self._cfg
        w0 = _lm_xy(hands[0], WRIST)
        w1 = _lm_xy(hands[1], WRIST)
        dist = _dist(w0, w1)
        angle = math.atan2(w1[1] - w0[1], w1[0] - w0[0])
        center = ((w0[0] + w1[0]) / 2, (w0[1] + w1[1]) / 2)

        t0 = self._trackers.get(labels[0], HandTracker())
        t1 = self._trackers.get(labels[1], HandTracker())

        # Distance change rate
        dist_delta = dist - self._prev_inter_hand_dist if self._prev_inter_hand_dist > 0 else 0
        angle_delta = angle - self._prev_inter_hand_angle

        thresh = cfg["TWO_HAND_VELOCITY_THRESHOLD"]
        clap_thresh = cfg["CLAP_COLLAPSE_VELOCITY"]

        # CLAP - fast collapse
        if self._check_hysteresis("clap", dist_delta < -clap_thresh and dist < 0.1):
            self._transition(GestureState.CLAP)
            self._mouse.press("enter")
            self._transition(GestureState.IDLE)
        # EXPAND
        elif self._check_hysteresis("expand", dist_delta > thresh * 0.02):
            self._transition(GestureState.EXPAND)
            scale = int(dist_delta * 500)
            self._mouse.resize_foreground_window(scale, scale)
        # COMPRESS
        elif self._check_hysteresis("compress", dist_delta < -thresh * 0.02):
            self._transition(GestureState.COMPRESS)
            scale = int(dist_delta * 500)
            self._mouse.resize_foreground_window(scale, scale)
        # ROTATE
        elif abs(angle_delta) > 0.03:
            self._transition(GestureState.ROTATE)
        # PUSH (parallel movement)
        elif self._prev_two_hand_center is not None:
            cdx = center[0] - self._prev_two_hand_center[0]
            cdy = center[1] - self._prev_two_hand_center[1]
            if math.hypot(cdx, cdy) > 0.01:
                self._transition(GestureState.PUSH)
                self._mouse.move_foreground_window(int(cdx * 1920), int(cdy * 1080))
        else:
            self._transition(GestureState.TWO_HAND)

        self._prev_inter_hand_dist = dist
        self._prev_inter_hand_angle = angle
        self._prev_two_hand_center = center


class GestureEngine:
    """Main gesture engine — captures webcam, runs MediaPipe HandLandmarker, drives state machine."""

    def __init__(self, mouse: MouseController, shared_state: Dict[str, Any],
                 enabled_event: threading.Event, stop_event: threading.Event) -> None:
        self._mouse = mouse
        self._shared = shared_state
        self._enabled = enabled_event
        self._stop = stop_event
        self._cfg = Config.get()
        self._state_machine = GestureStateMachine(mouse, shared_state)
        self._cap: Optional[cv2.VideoCapture] = None

    def run(self) -> None:
        """Main loop — call this from a thread."""
        logger.info("Gesture engine starting")
        cam_idx = self._cfg["CAMERA_INDEX"]
        fw = self._cfg["FRAME_WIDTH"]
        fh = self._cfg["FRAME_HEIGHT"]

        # --- Camera init (use DirectShow on Windows for reliability) ---
        self._shared["gesture_status"] = "Opening camera..."
        logger.info("Opening camera index %d with DirectShow backend", cam_idx)
        try:
            self._cap = cv2.VideoCapture(cam_idx, cv2.CAP_DSHOW)
            if not self._cap.isOpened():
                logger.warning("DirectShow failed, trying default backend")
                self._shared["gesture_status"] = "Trying alt camera..."
                self._cap = cv2.VideoCapture(cam_idx)
            if not self._cap.isOpened():
                logger.error("Cannot open camera index %d", cam_idx)
                self._shared["gesture_status"] = "Camera not found"
                return
            # FIX 2: Set camera to low res for speed
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, fw)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, fh)
            self._cap.set(cv2.CAP_PROP_FPS, 30)
            logger.info("Camera opened successfully (target %dx%d)", fw, fh)
        except Exception as exc:
            logger.error("Camera init failed: %s", exc)
            self._shared["gesture_status"] = f"Camera error: {exc}"
            return

        # --- MediaPipe HandLandmarker init (Tasks API) ---
        self._shared["gesture_status"] = "Loading hand model..."
        logger.info("Initializing MediaPipe HandLandmarker (Tasks API)")
        script_dir = os.path.dirname(os.path.abspath(__file__))
        model_path = os.path.join(script_dir, "hand_landmarker.task")

        if not os.path.exists(model_path):
            logger.error("Hand landmarker model not found at %s", model_path)
            self._shared["gesture_status"] = "Model not found"
            if self._cap is not None:
                self._cap.release()
            return

        try:
            base_options = mp_python.BaseOptions(model_asset_path=model_path)
            options = mp_vision.HandLandmarkerOptions(
                base_options=base_options,
                num_hands=1,  # FIX 2: single hand for speed (two-hand checked separately)
                min_hand_detection_confidence=0.5,
                min_hand_presence_confidence=0.5,
                min_tracking_confidence=0.4,
            )
            landmarker = mp_vision.HandLandmarker.create_from_options(options)
            logger.info("MediaPipe HandLandmarker initialized")
        except Exception as exc:
            logger.error("MediaPipe init failed: %s", exc)
            self._shared["gesture_status"] = f"MediaPipe error: {exc}"
            if self._cap is not None:
                self._cap.release()
            return

        # --- Test frame read ---
        self._shared["gesture_status"] = "Testing capture..."
        logger.info("Reading test frame")
        try:
            ret, test_frame = self._cap.read()
            if not ret or test_frame is None:
                logger.error("Test frame read failed — camera may be in use by another app")
                self._shared["gesture_status"] = "Camera busy/blocked"
                self._cap.release()
                return
            logger.info("Test frame OK: shape=%s", test_frame.shape)
        except Exception as exc:
            logger.error("Test frame failed: %s", exc)
            self._shared["gesture_status"] = f"Camera read error: {exc}"
            self._cap.release()
            return

        self._shared["gesture_status"] = "Running"
        logger.info("Gesture engine started — camera %d", cam_idx)
        prev_time = time.time()
        frame_count = 0
        skip_n = self._cfg["PROCESS_EVERY_N_FRAMES"]

        # FIX 2: Threaded pipeline — camera → mediapipe → gesture
        frame_queue: queue.Queue = queue.Queue(maxsize=2)
        result_queue: queue.Queue = queue.Queue(maxsize=2)
        pipeline_stop = threading.Event()

        def _camera_thread():
            """Read frames from camera, resize, put into queue."""
            cam_frame_count = 0
            while not pipeline_stop.is_set() and not self._stop.is_set():
                if not self._enabled.is_set():
                    self._stop.wait(timeout=0.05)
                    continue
                ret, frame = self._cap.read()
                if not ret:
                    time.sleep(0.005)
                    continue
                cam_frame_count += 1
                if cam_frame_count % skip_n != 0:
                    continue  # FIX 2: skip frames
                frame = cv2.flip(frame, 1)
                small = cv2.resize(frame, (fw, fh))  # FIX 2: resize
                rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
                try:
                    frame_queue.put_nowait((rgb, time.time()))
                except queue.Full:
                    try:
                        frame_queue.get_nowait()  # drop oldest
                    except queue.Empty:
                        pass
                    frame_queue.put_nowait((rgb, time.time()))

        def _mediapipe_thread():
            """Run MediaPipe detection on frames from queue."""
            while not pipeline_stop.is_set() and not self._stop.is_set():
                try:
                    rgb, ts = frame_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                try:
                    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                    result = landmarker.detect(mp_image)
                    try:
                        result_queue.put_nowait((result, ts))
                    except queue.Full:
                        try:
                            result_queue.get_nowait()
                        except queue.Empty:
                            pass
                        result_queue.put_nowait((result, ts))
                except Exception as exc:
                    logger.error("MediaPipe detect error: %s", exc)

        cam_t = threading.Thread(target=_camera_thread, daemon=True, name="CameraCapture")
        mp_t = threading.Thread(target=_mediapipe_thread, daemon=True, name="MediaPipeDetect")
        cam_t.start()
        mp_t.start()
        logger.info("Pipeline threads started (camera + mediapipe)")

        try:
            while not self._stop.is_set():
                if not self._enabled.is_set():
                    self._shared["gesture_status"] = "Paused"
                    self._stop.wait(timeout=0.1)
                    continue

                try:
                    result, ts = result_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                now = time.time()
                frame_time = now - prev_time
                prev_time = now

                self._state_machine.process_frame(result, frame_time)

                # FPS + latency
                latency_ms = (now - ts) * 1000
                fps = 1.0 / max(frame_time, 0.001)
                self._shared["fps"] = f"{fps:.0f} ({latency_ms:.0f}ms)"
                self._shared["gesture_status"] = "Running"
                frame_count += 1

        except Exception as exc:
            logger.error("Gesture engine crashed: %s", exc)
            self._shared["gesture_status"] = f"Error: {exc}"
        finally:
            pipeline_stop.set()
            cam_t.join(timeout=2)
            mp_t.join(timeout=2)
            landmarker.close()
            if self._cap is not None:
                self._cap.release()
            logger.info("Gesture engine stopped after %d frames", frame_count)
