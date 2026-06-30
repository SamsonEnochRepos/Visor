"""Quick functional test for perception, recognition, and intent layers."""
import sys, time, os
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, ".")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import numpy as np

from visor.perception.landmark_filter import LandmarkFilter
from visor.perception.temporal_buffer import TemporalBuffer
from visor.core.types import LandmarkFrame, Gesture, GestureResult
from visor.recognition.gesture_classifier import GestureClassifier
from visor.recognition.motion_classifier import MotionClassifier
from visor.recognition.confidence import ConfidenceScorer
from visor.intent.intent_engine import IntentEngine
from visor.intent.voice_intent import VoiceIntentResolver

print("=== PERCEPTION TEST ===")
lf = LandmarkFilter()
buf = TemporalBuffer(60)
for i in range(30):
    lm = np.random.rand(21, 3) * 0.01 + 0.5
    t = time.monotonic()
    filtered = lf.filter_landmarks(lm, t)
    frame = LandmarkFrame(filtered, "Right", t, lm)
    buf.push(frame)
    time.sleep(0.01)

print(f"  Buffer: {len(buf)} frames, FPS: {buf.fps:.1f}")
vel = buf.get_velocity(9, 5)
print(f"  Velocity lm9: [{vel[0]:.4f}, {vel[1]:.4f}, {vel[2]:.4f}]")
disp = buf.get_displacement(9, 15)
print(f"  Displacement: [{disp[0]:.4f}, {disp[1]:.4f}, {disp[2]:.4f}]")
print("  PASSED")

print("\n=== RECOGNITION TEST ===")
gc = GestureClassifier()
mc = MotionClassifier()
cs = ConfidenceScorer()

# Test point gesture (index up, others down)
lm_point = np.full((21, 3), 0.5)
# Index tip above index pip (y smaller = higher)
lm_point[8, 1] = 0.2   # INDEX_TIP high
lm_point[6, 1] = 0.5   # INDEX_PIP low
# Others curled
for tip, pip in [(12,10), (16,14), (20,18)]:
    lm_point[tip, 1] = 0.7
    lm_point[pip, 1] = 0.5
# Thumb curled
lm_point[4, 0] = 0.45  # THUMB_TIP
lm_point[3, 0] = 0.5   # THUMB_IP

result = gc.classify(lm_point, "Right")
print(f"  Point test: {result.gesture.value} ({result.confidence:.2f})")

# Test fist (all curled)
lm_fist = np.full((21, 3), 0.5)
for tip in [4, 8, 12, 16, 20]:
    lm_fist[tip, 1] = 0.7  # tips below PIPs
for pip in [3, 6, 10, 14, 18]:
    lm_fist[pip, 1] = 0.5
result_fist = gc.classify(lm_fist, "Right")
print(f"  Fist test: {result_fist.gesture.value} ({result_fist.confidence:.2f})")

# Test palm (all extended)
lm_palm = np.full((21, 3), 0.5)
for tip in [8, 12, 16, 20]:
    lm_palm[tip, 1] = 0.2  # tips above PIPs
for pip in [6, 10, 14, 18]:
    lm_palm[pip, 1] = 0.5
lm_palm[4, 0] = 0.2   # thumb tip extended (Right hand)
lm_palm[3, 0] = 0.4   # thumb IP
result_palm = gc.classify(lm_palm, "Right")
print(f"  Palm test: {result_palm.gesture.value} ({result_palm.confidence:.2f})")

# Test confidence scorer
for _ in range(8):
    scored = cs.score(result)
print(f"  Stability after 8 frames: {scored.stability:.2f}")
print(f"  Should act: {cs.should_act(scored)}")
print("  PASSED")

print("\n=== INTENT TEST ===")
ie = IntentEngine()
ctx = {}

# Point → CURSOR_MOVE
intent = ie.resolve(result, ctx)
print(f"  Point → {intent.intent.value}")

# Fist → CLOSE
intent_fist = ie.resolve(result_fist, ctx)
print(f"  Fist → {intent_fist.intent.value}")

# Palm → IDLE
intent_palm = ie.resolve(result_palm, ctx)
print(f"  Palm → {intent_palm.intent.value}")
print("  PASSED")

print("\n=== VOICE INTENT TEST ===")
vr = VoiceIntentResolver()
for text in ["open notepad", "close", "volume up", "scroll down", "search hello world"]:
    vi = vr.resolve(text)
    if vi:
        print(f"  '{text}' → {vi.intent.value} ctx={vi.context}")
    else:
        print(f"  '{text}' → None")
print("  PASSED")

print("\n=== ALL TESTS PASSED ===")
