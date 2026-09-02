# Neon Hand Trail

Move your index finger in front of your webcam and it leaves a glowing neon trail behind it, like light painting — using OpenCV + MediaPipe hand tracking.

## Requirements

- Python 3.11 or 3.12 (MediaPipe's legacy `solutions` API isn't supported on 3.13+)
- OpenCV
- MediaPipe 0.10.x (not 1.0+, which dropped the `solutions` API used here)
- NumPy

## Setup

```bash
py -3.11 -m pip install opencv-python mediapipe==0.10.14 numpy
```

## Run

```bash
py -3.11 neon_hand_trail.py
```

## Controls

| Key | Action |
|-----|--------|
| `q` | Quit |
| `c` | Clear the trail |
| `1`–`5` | Change trail color |
