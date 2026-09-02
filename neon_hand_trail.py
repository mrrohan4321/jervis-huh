"""
Neon Hand Trail Effect + Gesture Controls + Emotion Detection
----------------------------------------------------------------
Move your index finger in front of the webcam and it leaves a glowing
neon trail behind it, like light painting.

Gesture controls (pinch = thumb tip + index tip distance):
  Hand on RIGHT side of screen  -> leaves the neon trail + controls BRIGHTNESS
  Hand on LEFT side of screen   -> controls SYSTEM VOLUME (Windows only)

Also runs full facial emotion detection (happy/sad/angry/surprised/
neutral/fear/disgust) using the 'fer' library.

Keyboard controls:
  q       -> quit
  c       -> clear the trail
  1-5     -> change trail color
"""

import math
import os
import urllib.request
from collections import deque

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision as mp_vision

# Windows system volume control
from pycaw.pycaw import AudioUtilities

# Windows screen brightness control
import screen_brightness_control as sbc

# ---------- Hand tracking setup ----------
mp_hands = mp.solutions.hands
hands = mp_hands.Hands(
    max_num_hands=2,
    min_detection_confidence=0.7,
    min_tracking_confidence=0.7,
)

INDEX_TIP = mp_hands.HandLandmark.INDEX_FINGER_TIP
THUMB_TIP = mp_hands.HandLandmark.THUMB_TIP

# ---------- Emotion detector setup (MediaPipe Face Landmarker + blendshapes) ----------
FACE_MODEL_PATH = os.path.abspath("face_landmarker.task")
FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)

if not os.path.exists(FACE_MODEL_PATH):
    print("Downloading face landmark model (one-time, ~4MB)...")
    urllib.request.urlretrieve(FACE_MODEL_URL, FACE_MODEL_PATH)

# Read the model into memory and pass raw bytes instead of a file path —
# MediaPipe's path resolver has a known bug on Windows with drive-letter
# paths (e.g. "E:\...") that mangles them, so model_asset_buffer sidesteps it.
with open(FACE_MODEL_PATH, "rb") as _f:
    _face_model_bytes = _f.read()

face_base_options = mp_tasks.BaseOptions(model_asset_buffer=_face_model_bytes)
face_options = mp_vision.FaceLandmarkerOptions(
    base_options=face_base_options,
    output_face_blendshapes=True,
    output_facial_transformation_matrixes=False,
    num_faces=1,
    running_mode=mp_vision.RunningMode.IMAGE,
)
face_landmarker = mp_vision.FaceLandmarker.create_from_options(face_options)


def classify_emotion(blendshapes):
    """Turn MediaPipe face blendshape scores into a simple emotion label."""
    scores = {b.category_name: b.score for b in blendshapes}

    smile = (scores.get("mouthSmileLeft", 0) + scores.get("mouthSmileRight", 0)) / 2
    frown = (scores.get("mouthFrownLeft", 0) + scores.get("mouthFrownRight", 0)) / 2
    brow_down = (scores.get("browDownLeft", 0) + scores.get("browDownRight", 0)) / 2
    brow_up = scores.get("browInnerUp", 0)
    jaw_open = scores.get("jawOpen", 0)
    sneer = scores.get("noseSneerLeft", 0) + scores.get("noseSneerRight", 0)

    if smile > 0.4:
        return "Happy", smile
    if jaw_open > 0.5 and brow_up > 0.3:
        return "Surprised", max(jaw_open, brow_up)
    if sneer > 0.4:
        return "Disgust", sneer
    if brow_down > 0.4 and frown > 0.2:
        return "Angry", brow_down
    if frown > 0.35:
        return "Sad", frown
    return "Neutral", 1.0 - smile


# ---------- System volume setup (Windows) ----------
audio_device = AudioUtilities.GetSpeakers()

# ---------- Camera setup ----------
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)

# Trail = list of recent fingertip points
trail_length = 40
points = deque(maxlen=trail_length)

# Available neon colors (BGR)
colors = {
    "1": (255, 0, 255),   # magenta
    "2": (0, 255, 255),   # yellow
    "3": (255, 255, 0),   # cyan
    "4": (0, 255, 0),     # green
    "5": (0, 128, 255),   # orange
}
current_color = colors["1"]

# State
brightness = 0             # 0-100, last brightness value sent to Windows
last_sent_brightness = -1  # throttle: only call the OS API on real changes
emotion_label = "..."
emotion_score = 0.0
frame_count = 0
EMOTION_EVERY_N_FRAMES = 10  # emotion model is slow, don't run it every single frame


def draw_glow_trail(frame, points, color):
    """Draw the trail with a neon glow effect."""
    overlay = np.zeros_like(frame, dtype=np.uint8)

    for i in range(1, len(points)):
        if points[i - 1] is None or points[i] is None:
            continue
        thickness = int(np.interp(i, [0, len(points)], [2, 10]))
        cv2.line(overlay, points[i - 1], points[i], color, thickness)

    glow = cv2.GaussianBlur(overlay, (0, 0), sigmaX=15, sigmaY=15)
    frame = cv2.addWeighted(frame, 1.0, glow, 1.0, 0)
    frame = cv2.addWeighted(frame, 1.0, overlay, 1.0, 0)  # sharp core on top
    return frame


def pinch_distance(hand_landmarks, w, h):
    """Pixel distance between thumb tip and index fingertip."""
    thumb = hand_landmarks.landmark[THUMB_TIP]
    index = hand_landmarks.landmark[INDEX_TIP]
    x1, y1 = thumb.x * w, thumb.y * h
    x2, y2 = index.x * w, index.y * h
    return math.hypot(x2 - x1, y2 - y1)


while True:
    success, frame = cap.read()
    if not success:
        break

    frame = cv2.flip(frame, 1)  # mirror for natural movement
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    result = hands.process(rgb)

    h, w, _ = frame.shape
    fingertip = None

    if result.multi_hand_landmarks:
        for hand_landmarks in result.multi_hand_landmarks:
            # Decide which control this hand affects by WHERE it is on
            # screen, not by MediaPipe's left/right label — that label is
            # unreliable with flipped/mirrored webcam feeds.
            wrist = hand_landmarks.landmark[mp_hands.HandLandmark.WRIST]
            hand_x = wrist.x * w
            dist = pinch_distance(hand_landmarks, w, h)

            if hand_x >= w / 2:
                # Hand on the RIGHT side of the screen: trail + brightness
                lm = hand_landmarks.landmark[INDEX_TIP]
                fingertip = (int(lm.x * w), int(lm.y * h))

                brightness = int(np.clip(np.interp(dist, [20, 200], [0, 100]), 0, 100))
                # Only call the OS API when the value actually moved —
                # calling it every frame is slow and makes the video laggy.
                if abs(brightness - last_sent_brightness) >= 3:
                    try:
                        sbc.set_brightness(brightness)
                        last_sent_brightness = brightness
                    except Exception as e:
                        print("Brightness control failed:", e)

            else:
                # Hand on the LEFT side of the screen: volume
                vol_percent = int(np.clip(np.interp(dist, [20, 200], [0, 100]), 0, 100))
                try:
                    audio_device.volume_percent = vol_percent
                    print(f"Volume set to {vol_percent}%")  # debug — remove later if noisy
                except Exception as e:
                    print("Volume control failed:", e)

    points.append(fingertip)
    frame = draw_glow_trail(frame, points, current_color)

    if fingertip:
        cv2.circle(frame, fingertip, 8, (255, 255, 255), -1)

    # ---------- Emotion detection (throttled for performance) ----------
    frame_count += 1
    if frame_count % EMOTION_EVERY_N_FRAMES == 0:
        try:
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            face_result = face_landmarker.detect(mp_image)
            if face_result.face_blendshapes:
                blendshapes = face_result.face_blendshapes[0]
                emotion_label, emotion_score = classify_emotion(blendshapes)
            else:
                emotion_label = "no face"
                emotion_score = 0.0
        except Exception:
            pass  # skip a bad frame rather than crash the loop

    # ---------- HUD ----------
    cv2.line(frame, (w // 2, 0), (w // 2, h), (80, 80, 80), 1)  # zone divider
    cv2.putText(frame, "q: quit | c: clear | 1-5: color",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
    cv2.putText(frame, f"Volume (LEFT side): last set value shown in terminal",
                (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
    cv2.putText(frame, f"Brightness (RIGHT side): {brightness}%",
                (w // 2 + 10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
    cv2.putText(frame, f"Emotion: {emotion_label} ({emotion_score:.2f})",
                (10, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 150), 2)

    cv2.imshow("Neon Hand Trail", frame)

    key = cv2.waitKey(1) & 0xFF
    if key == ord("q"):
        break
    elif key == ord("c"):
        points.clear()
    elif chr(key) in colors:
        current_color = colors[chr(key)]

cap.release()
cv2.destroyAllWindows()