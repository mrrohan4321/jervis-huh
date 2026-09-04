# Gesture Control - Brightness & Volume (Iron Man / JARVIS HUD)

Webcam-based hand gesture control for your Windows screen brightness and system volume, with a Tony Stark-style holographic HUD.

- **Pinch, RIGHT side of screen** — brightness (gold ring)
- **Pinch, LEFT side of screen** — volume (cyan ring)
- **Make a fist** on either hand — instantly mutes that channel
- **Both hands hard-pinched at once** — SYNC: locks volume + brightness together
- **JARVIS boot sequence** on startup, spoken voice feedback, beeps, fading notification badges, idle "STANDBY" HUD when no hand is in frame

## Requirements

- Windows + Python 3.11 or 3.12 (MediaPipe's legacy `solutions` API isn't supported on 3.13+)
- OpenCV
- MediaPipe 0.10.x
- NumPy
- `pycaw` (system volume)
- `screen-brightness-control` (screen brightness)
- `pyttsx3` (voice feedback via Windows SAPI5 — Windows only)

## Setup

```bash
py -3.11 -m pip install opencv-python mediapipe==0.10.14 numpy
py -3.11 -m pip install pycaw screen-brightness-control pyttsx3
```

> `winsound` (beeps) is part of Python's standard library on Windows — nothing to install for that.
> Brightness control works on most laptop screens out of the box. External monitors need DDC/CI support (varies by monitor/graphics driver) — the terminal will print "Brightness control failed: ..." with the reason if it can't.

## Run

```bash
py -3.11 neon_hand_trail.py
```

## Controls

| Gesture | Action |
|---|---|
| `q` | Quit |
| Pinch (thumb + index) on the LEFT side | Adjust volume |
| Pinch (thumb + index) on the RIGHT side | Adjust brightness |
| Fist on either hand | Mute that hand's channel |
| Both hands hard-pinched together | SYNC volume + brightness to the same value |