"""
Neon Hand Trail Effect
-----------------------
Move your index finger in front of the webcam and it leaves a glowing
neon trail behind it, like light painting.

Controls:
  q       -> quit
  c       -> clear the trail
  1-5     -> change trail color
"""

import cv2
import numpy as np
import mediapipe as mp
from collections import deque

# ---------- Setup ----------
mp_hands = mp.solutions.hands
hands = mp_hands.Hands(
    max_num_hands=1,
    min_detection_confidence=0.7,
    min_tracking_confidence=0.7,
)

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

INDEX_FINGER_TIP = mp_hands.HandLandmark.INDEX_FINGER_TIP


def draw_glow_trail(frame, points, color):
    """Draw the trail with a neon glow effect."""
    overlay = np.zeros_like(frame, dtype=np.uint8)

    # Draw connecting lines with increasing thickness toward the newest point
    for i in range(1, len(points)):
        if points[i - 1] is None or points[i] is None:
            continue
        thickness = int(np.interp(i, [0, len(points)], [2, 10]))
        cv2.line(overlay, points[i - 1], points[i], color, thickness)

    # Create the glow by blurring the line layer and blending it additively
    glow = cv2.GaussianBlur(overlay, (0, 0), sigmaX=15, sigmaY=15)
    frame = cv2.addWeighted(frame, 1.0, glow, 1.0, 0)
    frame = cv2.addWeighted(frame, 1.0, overlay, 1.0, 0)  # sharp core on top
    return frame


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
        hand_landmarks = result.multi_hand_landmarks[0]
        lm = hand_landmarks.landmark[INDEX_FINGER_TIP]
        fingertip = (int(lm.x * w), int(lm.y * h))

    points.append(fingertip)

    frame = draw_glow_trail(frame, points, current_color)

    if fingertip:
        cv2.circle(frame, fingertip, 8, (255, 255, 255), -1)

    cv2.putText(frame, "q: quit | c: clear | 1-5: color",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

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