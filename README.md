# JERVIS - Gesture & Voice Control (Iron Man / JARVIS HUD)

Webcam-based hand gesture control for your Windows screen brightness and system volume, with a Tony Stark-style holographic HUD, spoken voice feedback, and voice commands.

- **Pinch, RIGHT side of screen** — brightness (gold ring)
- **Pinch, LEFT side of screen** — volume (cyan ring)
- **Make a fist** on either hand — instantly mutes that channel
- **Both hands hard-pinched at once** — SYNC: locks volume + brightness together
- **JARVIS boot sequence** on startup, spoken voice feedback (pyttsx3), confirmation beeps, fading notification badges, idle "STANDBY" HUD when no hand is in frame
- **Voice commands** via microphone: "volume up/down", "brightness up/down", "mute volume"/"mute brightness", "sync", "weather", "shutdown"/"exit"/"goodbye"
- **Weather + air quality report**, spoken on request or automatically after boot (via OpenWeatherMap)

## Requirements

- Windows + Python 3.11 or 3.12 (MediaPipe's legacy `solutions` API isn't supported on 3.13+)
- OpenCV
- MediaPipe 0.10.x
- NumPy
- `pycaw` (system volume)
- `screen-brightness-control` (screen brightness)
- `pyttsx3` (voice feedback via Windows SAPI5 — Windows only)
- `pywin32` (SAPI5/COM support for `pyttsx3` on Windows)
- `SpeechRecognition` + `pyaudio` (voice commands — needs a working microphone)
- `requests` (weather + air quality lookup)

## Setup

```bash
py -3.11 -m pip install opencv-python mediapipe==0.10.14 numpy
py -3.11 -m pip install pycaw screen-brightness-control pyttsx3 pywin32
py -3.11 -m pip install SpeechRecognition pyaudio requests
```

> If `pip install pyaudio` fails on Windows, try `pip install pipwin` then `pipwin install pyaudio`.
> `winsound` (beeps) is part of Python's standard library on Windows — nothing to install for that.
> Brightness control works on most laptop screens out of the box. External monitors need DDC/CI support (varies by monitor/graphics driver) — the terminal will print "Brightness control failed: ..." with the reason if it can't.
> Voice commands and weather reports are optional — if `SpeechRecognition`/`pyaudio` aren't installed or no microphone is found, the script prints a message and disables voice input instead of crashing.

### Weather setup (optional)

To enable spoken weather + air quality reports, open `JERVIS.py` and set your free [OpenWeatherMap](https://openweathermap.org/api) API key and city near the top of the file:

```python
WEATHER_API_KEY = "YOUR_OPENWEATHERMAP_API_KEY"
WEATHER_CITY = "Kolkata"
```

## Run

```bash
py -3.11 JERVIS.py
```

## Controls

| Gesture / Key | Action |
|---|---|
| Pinch (thumb + index) on the LEFT side | Adjust volume |
| Pinch (thumb + index) on the RIGHT side | Adjust brightness |
| Fist on either hand | Mute that hand's channel |
| Both hands hard-pinched together | SYNC volume + brightness to the same value |
| `q` | Quit (speaks a farewell first) |
| `w` | Manually announce current weather + air quality |

## Voice commands

Spoken (not typed) via your default microphone:

| Phrase | Action |
|---|---|
| "volume up" / "volume down" | Nudge system volume by 10% |
| "brightness up" / "brightness down" | Nudge screen brightness by 10% |
| "mute volume" / "mute brightness" | Mute that channel |
| "sync" | Lock volume + brightness together |
| "weather" | Speak current weather + air quality |
| "shutdown" / "exit" / "goodbye" | Quit the program |
