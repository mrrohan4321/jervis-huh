"""
Boot-time face recognition -- "who's my master" -- reusing the face
samples enroll_face.py already captures into face_data/<name>/*.jpg.

Trains an LBPH recognizer fresh on every startup (fast enough for the
few dozen images enroll_face.py collects -- under a second) then scans
the webcam for a few seconds looking for a confident match.

Deliberately defensive: no webcam, no enrolled faces, no opencv-contrib
(cv2.face), or nobody recognized within CONFIDENCE_THRESHOLD all just
fall through to `None`, which callers treat as "greet generically" --
this must never block or crash startup.
"""
import time
from pathlib import Path

import cv2
import numpy as np

FACE_DATA_DIR = Path(__file__).resolve().parent / "face_data"
CASCADE_FILENAME = "haarcascade_frontalface_default.xml"

# LBPH confidence is a DISTANCE, not a percentage -- LOWER means a
# better match. 70 is a reasonable "probably them" cutoff for the
# small/fast recognizer; lower it if it's greeting the wrong person,
# raise it if it's failing to recognize the right one.
CONFIDENCE_THRESHOLD = 70
SCAN_SECONDS = 4


def _get_cascade():
    bundled = Path(cv2.data.haarcascades) / CASCADE_FILENAME
    if bundled.is_file():
        c = cv2.CascadeClassifier(str(bundled))
        if not c.empty():
            return c
    local = Path(__file__).resolve().parent / CASCADE_FILENAME
    if local.is_file():
        c = cv2.CascadeClassifier(str(local))
        if not c.empty():
            return c
    return None


def _train_recognizer():
    """Returns (recognizer, {label_id: name}), or (None, {}) if
    there's nothing enrolled yet or opencv-contrib's face module isn't
    installed (cv2.face needs opencv-contrib-python specifically, see
    README)."""
    if not FACE_DATA_DIR.is_dir():
        return None, {}
    people = sorted(p for p in FACE_DATA_DIR.iterdir() if p.is_dir())
    if not people:
        return None, {}

    try:
        recognizer = cv2.face.LBPHFaceRecognizer_create()
    except AttributeError:
        print("Face ID: cv2.face missing -- need opencv-contrib-python, not plain opencv-python. Skipping.")
        return None, {}

    images, labels, names = [], [], {}
    for i, person_dir in enumerate(people):
        names[i] = person_dir.name
        for img_path in person_dir.glob("*.jpg"):
            img = cv2.imread(str(img_path), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                images.append(img)
                labels.append(i)

    if not images:
        return None, {}

    recognizer.train(images, np.array(labels))
    return recognizer, names


def identify_master(timeout=SCAN_SECONDS):
    """Scans the webcam briefly and returns the recognized name, or
    None. Never raises -- any failure just means 'greet generically'."""
    try:
        cascade = _get_cascade()
        if cascade is None:
            return None

        recognizer, names = _train_recognizer()
        if recognizer is None:
            return None

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return None

        found = None
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                ok, frame = cap.read()
                if not ok:
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5)
                if len(faces) == 0:
                    continue
                x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                label, confidence = recognizer.predict(gray[y:y + h, x:x + w])
                if confidence < CONFIDENCE_THRESHOLD:
                    found = names.get(label)
                    break
        finally:
            cap.release()
        return found
    except Exception as e:
        print("Face ID skipped:", e)
        return None
