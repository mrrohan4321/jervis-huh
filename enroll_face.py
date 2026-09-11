"""
Face enrollment helper for JERVIS.py
-------------------------------------
Captures face samples from your webcam and saves them into
face_data/<name>/*.jpg so the main script can recognize you at boot
and greet you by name instead of "sir".

Run this ONCE per person you want recognized:

    py -3.11 enroll_face.py

Controls:
  s  -> save the current frame (only works while a face is detected)
  q  -> finish and quit

Aim for at least 20-30 samples, moving your head slightly (angle,
distance, expression) between saves for a more reliable recognizer.
"""

from pathlib import Path

import cv2
import requests

FACE_DATA_DIR = Path("face_data")
SAMPLES_TARGET = 25

CASCADE_FILENAME = "haarcascade_frontalface_default.xml"
CASCADE_URL = ("https://raw.githubusercontent.com/opencv/opencv/master/"
               "data/haarcascades/haarcascade_frontalface_default.xml")


def get_face_cascade():
    """Load the Haar cascade for face detection. Some opencv-contrib-python
    installs on Windows ship without the bundled cascade XML files
    (cv2.data.haarcascades points at a file that doesn't exist), so
    fall back to downloading it once from OpenCV's GitHub repo and
    caching it next to this script."""
    bundled_path = cv2.data.haarcascades + CASCADE_FILENAME
    if Path(bundled_path).is_file():
        cascade = cv2.CascadeClassifier(bundled_path)
        if not cascade.empty():
            return cascade

    local_path = Path(__file__).resolve().parent / CASCADE_FILENAME
    if not local_path.is_file():
        print("Haar cascade not found in the opencv install — downloading it once...")
        r = requests.get(CASCADE_URL, timeout=15)
        r.raise_for_status()
        local_path.write_bytes(r.content)
        print(f"Saved cascade to {local_path}")

    cascade = cv2.CascadeClassifier(str(local_path))
    if cascade.empty():
        raise RuntimeError(f"Failed to load Haar cascade from {local_path}")
    return cascade


def main():
    name = input("Name to enroll (used as the greeting name): ").strip()
    if not name:
        print("No name entered, aborting.")
        return

    person_dir = FACE_DATA_DIR / name
    person_dir.mkdir(parents=True, exist_ok=True)
    existing = len(list(person_dir.glob("*.jpg")))

    try:
        face_cascade = get_face_cascade()
    except Exception as e:
        print("Could not load the face detector:", e)
        print("Check your internet connection (needed once, to download the cascade file) and try again.")
        return

    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)

    saved = existing
    print(f"Enrolling '{name}'. Press 's' to save a sample, 'q' to finish.")
    print(f"Target: ~{SAMPLES_TARGET} samples (already have {existing}).")

    while True:
        success, frame = cap.read()
        if not success:
            break
        frame = cv2.flip(frame, 1)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5)

        display = frame.copy()
        for (x, y, w, h) in faces:
            cv2.rectangle(display, (x, y), (x + w, y + h), (30, 190, 255), 2)

        cv2.putText(display, f"Saved: {saved}/{SAMPLES_TARGET}   s: save   q: quit",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.imshow("Face Enrollment", display)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif key == ord("s"):
            if len(faces) == 0:
                print("No face detected — try again.")
                continue
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            face_crop = gray[y:y + h, x:x + w]
            out_path = person_dir / f"{saved:03d}.jpg"
            cv2.imwrite(str(out_path), face_crop)
            saved += 1
            print(f"Saved {out_path} ({saved} total)")
            if saved >= SAMPLES_TARGET:
                print("Reached target sample count — you can quit with 'q' now, "
                      "or keep going for extra robustness.")

    cap.release()
    cv2.destroyAllWindows()
    print(f"Done. {saved} samples saved for '{name}' in {person_dir}/")


if __name__ == "__main__":
    main()