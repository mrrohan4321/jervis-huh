# Neon Hand Trail

Move your index finger in front of your webcam and it leaves a glowing neon trail behind it, like light painting — using OpenCV + MediaPipe hand tracking.

Also includes:
- **Pinch-gesture brightness control** (right hand thumb+index pinch)
- **Pinch-gesture system volume control** (left hand thumb+index pinch, Windows only)
- **Full facial emotion detection** (happy/sad/angry/surprised/neutral/fear/disgust) via the `fer` library

## Requirements

- Python 3.11 or 3.12 (MediaPipe's legacy `solutions` API isn't supported on 3.13+)
- OpenCV
- MediaPipe 0.10.x (not 1.0+, which dropped the `solutions` API used here)
- NumPy
- `fer` (emotion detection) + `tensorflow` + `moviepy`
- `pycaw` + `comtypes` (Windows system volume control)

## Setup

```bash
py -3.11 -m pip install opencv-python mediapipe==0.10.14 numpy
py -3.11 -m pip install fer tensorflow moviepy
py -3.11 -m pip install pycaw comtypes
```

> Note: `fer`/`tensorflow` are heavier installs and may take a few minutes.
> Volume control (`pycaw`) is Windows-only — on Mac/Linux, remove that part of the script or it will error at startup.

## Run

```bash
py -3.11 neon_hand_trail.py
```

## Controls

| Key / Gesture | Action |
|---|---|
| `q` | Quit |
| `c` | Clear the trail |
| `1`–`5` | Change trail color |
| Right hand pinch (thumb + index) | Adjust brightness |
| Left hand pinch (thumb + index) | Adjust system volume |
| Face in frame | Live emotion label shown on screen |
