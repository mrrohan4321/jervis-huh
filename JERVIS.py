"""
Gesture Control - Brightness & Volume (Iron Man / JARVIS HUD style)
--------------------------------------------------------------------
Webcam-based hand gesture control for your Windows screen brightness and
system volume, with an animated Tony Stark-style holographic ring drawn
around the pinch point.

Gesture controls (pinch = thumb tip + index tip distance):
  Hand on the RIGHT side of the screen  -> controls BRIGHTNESS
  Hand on the LEFT side of the screen   -> controls SYSTEM VOLUME

Extra gestures:
  Closed fist on either hand   -> instantly mutes that hand's channel
                                   (volume -> 0 / brightness -> 0)
  Both hands pinched hard      -> "SYNC" gesture: locks volume AND
                                   brightness to the same value at once
  L-shape (thumb + index, one hand)   -> hold ~1.4s to LOCK THE PC
  Peace sign (index+middle, one hand) -> hold ~0.8s to take a SCREENSHOT
                                           (with a camera-shutter flash
                                           and shutter-click sound)
  Two open palms (both hands)         -> hold ~1.2s to launch CHROME
  Two peace signs (both hands)        -> hold ~1.2s to launch SPOTIFY

  Every hold-to-confirm gesture above (lock / screenshot / app launch)
  shows an animated "target lock" scan effect (converging corner
  brackets + a spinning progress ring) while it's held, and each has
  its own cooldown afterward so it can't re-fire every single frame.

Feedback:
  - Animated arc-reactor style ring (fills with %, rotating tick marks)
  - Animated "target lock" scan effect for hold-to-confirm gestures
  - Fading on-screen notification badges (like the native OS volume popup)
  - Camera-shutter white flash + click sound on screenshot capture
  - Spoken voice feedback via pyttsx3, running in its OWN OS process
    (see _tts_worker_process below) so OpenCV/MediaPipe running in the
    main process can never interfere with the SAPI5 COM apartment.

Voice commands (spoken, not typed — needs a working microphone):
  "volume up" / "volume down"        -> nudge system volume by 10%
  "brightness up" / "brightness down"-> nudge screen brightness by 10%
  "mute volume" / "mute brightness"  -> mute that channel
  "sync"                             -> lock volume + brightness together
  "weather"                          -> speak current weather + air quality
  "news"                             -> speak today's top headlines
  "jarvis <question>"                -> ask anything (time, date, or a
                                          real chatbot answer via Groq's
                                          free LLM API — see GROQ_API_KEY)
  "shutdown" / "exit" / "goodbye"    -> quit the program

Keyboard controls:
  q  -> quit (speaks a farewell first)
  w  -> manually announce current weather + air quality
  n  -> manually announce today's news headlines

Live suit diagnostics (bottom-left HUD panel):
  CPU / RAM / battery usage, refreshed roughly once a second via psutil.

Face-recognition greeting:
  If face_data/<name>/ folders (built with enroll_face.py) exist, the
  boot greeting uses your name instead of "sir" when it recognizes you
  from the webcam. No enrolled faces -> falls back to "sir" as before.

Requires (on top of opencv-contrib-python/mediapipe/pycaw/screen_brightness_control):
  pip install pyttsx3 pywin32 SpeechRecognition pyaudio requests psutil pillow
  (if `pip install pyaudio` fails on Windows, try `pip install pipwin`
  then `pipwin install pyaudio`)
  Pillow (PIL.ImageGrab) is used for the screenshot gesture.

IMPORTANT — face recognition needs opencv-CONTRIB (cv2.face lives there,
not in plain opencv-python). If you already have opencv-python installed:
  pip uninstall opencv-python
  pip install opencv-contrib-python
"""

import ctypes
import json
import math
import multiprocessing
import os
import queue
import random
import subprocess
import threading
import time
import winsound
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import mediapipe as mp
import psutil
import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from PIL import ImageGrab
from pydantic import BaseModel

# Windows system volume control
from pycaw.pycaw import AudioUtilities

# Windows screen brightness control
import screen_brightness_control as sbc

# ==================================================================
#  Config — everything sensitive is read from the environment / .env
#  file, NEVER hardcoded here. Copy .env.example to .env and fill in
#  your own values (see README.md).
# ==================================================================
load_dotenv()

# ---------- Weather + air quality config (OpenWeatherMap) ----------
# Free API key: https://openweathermap.org/api
# Location is auto-detected from your IP address (see get_location()
# below) — WEATHER_CITY is only used as a fallback if that
# auto-detection fails (e.g. no internet route to the geolocation service).
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "")
WEATHER_CITY = os.getenv("WEATHER_CITY", "Kolkata")

# ---------- "Ask JARVIS anything" config (Groq — free LLM API) ----------
# Free key: console.groq.com -> sign up -> API Keys -> Create key.
# No credit card needed for the free tier. If left unset, "jarvis <q>"
# falls back to a much more limited DuckDuckGo instant-answer lookup.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

# ---------- Local FastAPI server (REST + WebSocket) ----------
# Lets a frontend drive JARVIS with text commands and receive the same
# wake / heard / reply events the voice pipeline produces, without
# needing a mic. See server setup near the bottom of this file.
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))

# ---------- Face recognition config ----------
# Build this folder with enroll_face.py first: face_data/<name>/*.jpg
# for each person you want recognized. If the folder is empty/missing,
# the boot greeting just falls back to "sir" as before.
FACE_DATA_DIR = Path("face_data")
FACE_CONFIDENCE_THRESHOLD = 75  # LBPH distance — LOWER is a better match

# ---------- Live system stats HUD ----------
STATS_REFRESH_INTERVAL = 1.0  # seconds between CPU/RAM/battery polls

# ---------- Hold-to-confirm gesture config ----------
# Lock PC / launch app / screenshot gestures all require the pose to be
# HELD for a bit (rather than firing instantly) so a hand passing
# through the pose on its way to something else doesn't trigger them
# by accident. ACTION_COOLDOWN then blocks immediate re-triggering.
LOCK_HOLD_SECONDS = 1.4
APP_LAUNCH_HOLD_SECONDS = 1.2
SCREENSHOT_HOLD_SECONDS = 0.8
ACTION_COOLDOWN = 4.0  # seconds before the same gesture can fire again
SCREENSHOT_FLASH_SECONDS = 0.15
SCREENSHOT_DIR = Path("gesture_screenshots")

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

# ---------- Hand tracking landmark constants (cheap, no model load) ----------
mp_hands = mp.solutions.hands
INDEX_TIP = mp_hands.HandLandmark.INDEX_FINGER_TIP
INDEX_PIP = mp_hands.HandLandmark.INDEX_FINGER_PIP
MIDDLE_TIP = mp_hands.HandLandmark.MIDDLE_FINGER_TIP
MIDDLE_PIP = mp_hands.HandLandmark.MIDDLE_FINGER_PIP
RING_TIP = mp_hands.HandLandmark.RING_FINGER_TIP
RING_PIP = mp_hands.HandLandmark.RING_FINGER_PIP
PINKY_TIP = mp_hands.HandLandmark.PINKY_TIP
PINKY_PIP = mp_hands.HandLandmark.PINKY_PIP
THUMB_TIP = mp_hands.HandLandmark.THUMB_TIP
THUMB_IP = mp_hands.HandLandmark.THUMB_IP
WRIST = mp_hands.HandLandmark.WRIST

# ---------- HUD colors (BGR) ----------
GOLD = (30, 190, 255)     # brightness
CYAN = (255, 220, 40)     # volume / screenshot
WHITE = (255, 255, 255)
RED = (60, 60, 255)       # mute / lock
MAGENTA = (255, 60, 220)  # sync
GREEN = (80, 220, 80)     # spotify launch

# ==================================================================
#  Voice feedback (pyttsx3) — runs in its OWN OS PROCESS, not a
#  thread. cv2/mediapipe loaded in the main process can silently break
#  SAPI5's COM apartment even across threads with CoInitialize(), so
#  the only fully reliable fix is to isolate TTS in a separate process
#  that never imports those heavy libraries at all.
# ==================================================================
_tts_queue = None  # multiprocessing.Queue, created in main()


def _tts_worker_process(q):
    """Entry point for the dedicated TTS process. Runs in total
    isolation from cv2/mediapipe — only pyttsx3 is imported here.

    Known pyttsx3/SAPI5 bug: reusing one engine instance across
    multiple say()+runAndWait() calls means only the FIRST call
    reliably produces audio — later calls on the same engine can go
    silent with no exception raised. The fix is to build a brand new
    engine for every single utterance, so each call is always that
    engine's "first" (and only) call.
    """
    import pyttsx3

    time.sleep(0.3)  # let SAPI5 finish initializing voices

    while True:
        text = q.get()
        if text is None:
            break
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", 175)
            engine.setProperty("volume", 1.0)
            engine.say(text)
            engine.runAndWait()
            del engine
        except Exception as e:
            print("TTS failed:", e)


def speak(text):
    """Non-blocking: drop the message instead of freezing the video
    loop if speech is already backed up, or if the TTS process hasn't
    been started yet."""
    if _tts_queue is None:
        return
    try:
        _tts_queue.put_nowait(text)
    except Exception:
        pass


def beep(freq=900, duration=80):
    """Short confirmation blip, played on its own throwaway thread so
    winsound.Beep's blocking call never stalls the video loop. Plain
    winsound beeps aren't COM-based, so a thread is fine here."""
    def _play():
        try:
            winsound.Beep(freq, duration)
        except Exception as e:
            print("Beep failed:", e)
    threading.Thread(target=_play, daemon=True).start()


def shutter_sound():
    """Two-tone camera-shutter click, played on its own thread (same
    reasoning as beep() above) so it never blocks the video loop."""
    def _play():
        try:
            winsound.Beep(1800, 40)
            winsound.Beep(1200, 60)
        except Exception as e:
            print("Shutter sound failed:", e)
    threading.Thread(target=_play, daemon=True).start()


# ==================================================================
#  On-screen fading notification badges (like the native OS popup)
# ==================================================================
notifications = []  # list of dicts: text, color, start, duration


def add_notification(text, color, duration=1.6):
    notifications.append({
        "text": text,
        "color": color,
        "start": time.time(),
        "duration": duration,
    })
    if len(notifications) > 4:
        notifications.pop(0)


def draw_notifications(frame):
    w = frame.shape[1]
    now = time.time()
    alive = []
    y = 90
    for note in notifications:
        t = now - note["start"]
        dur = note["duration"]
        if t >= dur:
            continue  # expired, drop it
        alive.append(note)

        fade_in, fade_out = 0.15, 0.35
        if t < fade_in:
            alpha = t / fade_in
        elif t > dur - fade_out:
            alpha = max(0.0, (dur - t) / fade_out)
        else:
            alpha = 1.0

        text = note["text"]
        color = note["color"]
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2)
        box_w, box_h = tw + 40, th + 24
        x = (w - box_w) // 2
        # slight rise as it fades in, like a native popup
        rise = int(10 * (1 - min(1.0, t / fade_in))) if t < fade_in else 0
        by = y - rise

        overlay = frame.copy()
        cv2.rectangle(overlay, (x, by), (x + box_w, by + box_h), (20, 20, 20), -1)
        cv2.rectangle(overlay, (x, by), (x + box_w, by + box_h), color, 2)
        cv2.putText(overlay, text, (x + 20, by + box_h - 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2, cv2.LINE_AA)
        cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)

        y += box_h + 12

    notifications[:] = alive


# ==================================================================
#  Geometry helpers
# ==================================================================
def pinch_distance(hand_landmarks, w, h):
    thumb = hand_landmarks.landmark[THUMB_TIP]
    index = hand_landmarks.landmark[INDEX_TIP]
    x1, y1 = thumb.x * w, thumb.y * h
    x2, y2 = index.x * w, index.y * h
    return math.hypot(x2 - x1, y2 - y1)


def is_fist(hand_landmarks):
    """Heuristic: a finger is 'curled' if its tip sits below (in image
    y) its own PIP joint. Fist = at least 3 of the 4 non-thumb fingers
    curled. Assumes a roughly upright, camera-facing hand."""
    pairs = [
        (INDEX_TIP, INDEX_PIP),
        (MIDDLE_TIP, MIDDLE_PIP),
        (RING_TIP, RING_PIP),
        (PINKY_TIP, PINKY_PIP),
    ]
    curled = 0
    for tip, pip in pairs:
        if hand_landmarks.landmark[tip].y > hand_landmarks.landmark[pip].y:
            curled += 1
    return curled >= 3


def finger_extended(hand_landmarks, tip, pip):
    """A finger is 'extended' if its tip sits above (in image y) its
    own PIP joint. Shared helper for the pose-detection functions
    below (mirrors the curl check inside is_fist)."""
    return hand_landmarks.landmark[tip].y < hand_landmarks.landmark[pip].y


def is_open_palm(hand_landmarks):
    """All four non-thumb fingers extended — a flat, raised hand.
    Used (on both hands at once) as the CHROME-launch gesture."""
    fingers = [(INDEX_TIP, INDEX_PIP), (MIDDLE_TIP, MIDDLE_PIP),
               (RING_TIP, RING_PIP), (PINKY_TIP, PINKY_PIP)]
    return all(finger_extended(hand_landmarks, tip, pip) for tip, pip in fingers)


def is_peace_sign(hand_landmarks):
    """Index + middle extended, ring + pinky curled — a 'V'/peace sign.
    One hand -> SCREENSHOT. Both hands at once -> SPOTIFY launch."""
    index_ext = finger_extended(hand_landmarks, INDEX_TIP, INDEX_PIP)
    middle_ext = finger_extended(hand_landmarks, MIDDLE_TIP, MIDDLE_PIP)
    ring_curled = not finger_extended(hand_landmarks, RING_TIP, RING_PIP)
    pinky_curled = not finger_extended(hand_landmarks, PINKY_TIP, PINKY_PIP)
    return index_ext and middle_ext and ring_curled and pinky_curled


def is_l_shape(hand_landmarks):
    """Rough 'L' shape: index extended, middle/ring/pinky curled, and
    the thumb spread out sideways away from the palm (rather than
    tucked in, as it would be in a plain fist). Used as the LOCK PC
    gesture. This deliberately takes priority over is_fist's
    classification when both could apply — see the main loop, where
    a hand's "fist" flag is cleared if this is also true."""
    index_ext = finger_extended(hand_landmarks, INDEX_TIP, INDEX_PIP)
    middle_curled = not finger_extended(hand_landmarks, MIDDLE_TIP, MIDDLE_PIP)
    ring_curled = not finger_extended(hand_landmarks, RING_TIP, RING_PIP)
    pinky_curled = not finger_extended(hand_landmarks, PINKY_TIP, PINKY_PIP)
    thumb_tip = hand_landmarks.landmark[THUMB_TIP]
    thumb_ip = hand_landmarks.landmark[THUMB_IP]
    thumb_spread = abs(thumb_tip.x - thumb_ip.x) > 0.045
    return index_ext and middle_curled and ring_curled and pinky_curled and thumb_spread


# ==================================================================
#  HUD drawing
# ==================================================================
def draw_glow_circle(frame, center, radius, color, thickness=2, layers=4):
    overlay = frame.copy()
    for i in range(layers, 0, -1):
        alpha = 0.10 * (layers - i + 1) / layers
        cv2.circle(overlay, center, radius + i * 4, color, thickness + i, cv2.LINE_AA)
        cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)
    cv2.circle(frame, center, radius, color, thickness, cv2.LINE_AA)


def draw_arc_ring(frame, center, radius, percent, color, spin_angle, muted=False):
    cx, cy = center

    overlay = frame.copy()
    cv2.circle(overlay, center, radius, color, 1, cv2.LINE_AA)
    cv2.addWeighted(overlay, 0.35, frame, 0.65, 0, frame)

    start_angle = -90
    end_angle = start_angle + int(360 * (percent / 100.0))
    ring_color = RED if muted else color
    cv2.ellipse(frame, center, (radius, radius), 0, start_angle, end_angle,
                ring_color, 3, cv2.LINE_AA)

    theta = math.radians(end_angle)
    dot = (int(cx + radius * math.cos(theta)), int(cy + radius * math.sin(theta)))
    cv2.circle(frame, dot, 5, WHITE, -1, cv2.LINE_AA)
    cv2.circle(frame, dot, 8, ring_color, 1, cv2.LINE_AA)

    tick_count = 12
    inner_r = radius + 10
    outer_r = radius + 18
    for i in range(tick_count):
        ang = math.radians(spin_angle + i * (360 / tick_count))
        x1 = int(cx + inner_r * math.cos(ang))
        y1 = int(cy + inner_r * math.sin(ang))
        x2 = int(cx + outer_r * math.cos(ang))
        y2 = int(cy + outer_r * math.sin(ang))
        cv2.line(frame, (x1, y1), (x2, y2), color, 1, cv2.LINE_AA)

    for ang_deg in (0, 90, 180, 270):
        ang = math.radians(ang_deg)
        x1 = int(cx + (radius - 6) * math.cos(ang))
        y1 = int(cy + (radius - 6) * math.sin(ang))
        x2 = int(cx + (radius + 6) * math.cos(ang))
        y2 = int(cy + (radius + 6) * math.sin(ang))
        cv2.line(frame, (x1, y1), (x2, y2), WHITE, 1, cv2.LINE_AA)

    if muted:
        cv2.line(frame, (cx - radius, cy - radius), (cx + radius, cy + radius), RED, 2, cv2.LINE_AA)


def draw_target_lock(frame, center, progress, color, label):
    """Animated 'target lock' scan effect used by every hold-to-confirm
    gesture (lock PC, launch app, screenshot): four corner brackets
    converge toward the target point as progress goes 0 -> 1, a
    spinning outer ring shrinks slightly, and a white progress arc
    sweeps around it. When progress reaches 1.0 the target flashes
    with a bright glow to mark the moment the action fires."""
    cx, cy = center
    t = time.time()
    max_offset = 90
    offset = int(max_offset * (1 - progress)) + 20
    size = 18
    spin = (t * 200) % 360

    corners = [(-1, -1), (1, -1), (-1, 1), (1, 1)]
    for dx, dy in corners:
        bx, by = cx + dx * offset, cy + dy * offset
        cv2.line(frame, (bx, by), (bx - dx * size, by), color, 2, cv2.LINE_AA)
        cv2.line(frame, (bx, by), (bx, by - dy * size), color, 2, cv2.LINE_AA)

    ring_r = int(70 - 15 * progress)
    cv2.ellipse(frame, center, (ring_r, ring_r), spin, 0, 100, color, 2, cv2.LINE_AA)
    cv2.ellipse(frame, center, (ring_r, ring_r), spin + 180, 0, 100, color, 2, cv2.LINE_AA)
    cv2.ellipse(frame, center, (max(ring_r - 10, 5), max(ring_r - 10, 5)), -90, 0,
                int(360 * progress), WHITE, 2, cv2.LINE_AA)

    pct = int(progress * 100)
    (label_w, _), _ = cv2.getTextSize(f"{label} {pct}%", cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
    cv2.putText(frame, f"{label} {pct}%", (cx - label_w // 2, cy + ring_r + 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)

    if progress >= 1.0:
        draw_glow_circle(frame, center, ring_r + 10, WHITE, thickness=3, layers=5)


def put_hud_text(frame, text, org, color, scale=0.55, thickness=1):
    cv2.putText(frame, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color,
                thickness, cv2.LINE_AA)


# ==================================================================
#  HUD chrome — scanlines, grid, corner brackets, idle standby
# ==================================================================
def draw_scanlines(frame, spacing=4, alpha=0.06):
    """Faint horizontal scanline overlay for a holographic feel."""
    overlay = frame.copy()
    h, w = frame.shape[:2]
    for y in range(0, h, spacing):
        cv2.line(overlay, (0, y), (w, y), (0, 0, 0), 1)
    cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)


def draw_hud_grid(frame, color=(60, 60, 60), spacing=80, alpha=0.12):
    """Faint background grid, like a targeting HUD."""
    overlay = frame.copy()
    h, w = frame.shape[:2]
    for x in range(0, w, spacing):
        cv2.line(overlay, (x, 0), (x, h), color, 1, cv2.LINE_AA)
    for y in range(0, h, spacing):
        cv2.line(overlay, (0, y), (w, y), color, 1, cv2.LINE_AA)
    cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)


def draw_corner_brackets(frame, color=GOLD, length=28, thickness=2, margin=14):
    """Target-lock style brackets in the four corners of the frame."""
    h, w = frame.shape[:2]
    corners = [
        (margin, margin, 1, 1),
        (w - margin, margin, -1, 1),
        (margin, h - margin, 1, -1),
        (w - margin, h - margin, -1, -1),
    ]
    for x, y, dx, dy in corners:
        cv2.line(frame, (x, y), (x + dx * length, y), color, thickness, cv2.LINE_AA)
        cv2.line(frame, (x, y), (x, y + dy * length), color, thickness, cv2.LINE_AA)


def draw_standby(frame, t):
    """Idle HUD shown while no hand is detected — slow pulsing core with
    two counter-rotating arcs, like JARVIS 'awaiting input'."""
    h, w = frame.shape[:2]
    cx, cy = w // 2, h // 2
    pulse = int(48 + 10 * math.sin(t * 2.2))
    draw_glow_circle(frame, (cx, cy), pulse, CYAN, thickness=1, layers=3)
    ring_angle = (t * 35) % 360
    cv2.ellipse(frame, (cx, cy), (pulse + 16, pulse + 16), ring_angle, 0, 140,
                GOLD, 2, cv2.LINE_AA)
    cv2.ellipse(frame, (cx, cy), (pulse + 16, pulse + 16), ring_angle + 180, 0, 140,
                GOLD, 2, cv2.LINE_AA)
    put_hud_text(frame, "STANDBY", (cx - 45, cy + pulse + 45), CYAN, 0.6, 2)
    put_hud_text(frame, "AWAITING HAND INPUT", (cx - 108, cy + pulse + 70),
                 (150, 150, 150), 0.5, 1)


def draw_stat_bar(frame, x, y, width, height, percent, color, label):
    """One 'suit diagnostics' style gauge: a label, a thin fill bar,
    and a percentage readout — used for CPU / RAM / battery."""
    pct = int(np.clip(percent, 0, 100))
    cv2.rectangle(frame, (x, y), (x + width, y + height), (60, 60, 60), 1, cv2.LINE_AA)
    fill_w = int(width * pct / 100.0)
    if fill_w > 0:
        cv2.rectangle(frame, (x, y), (x + fill_w, y + height), color, -1, cv2.LINE_AA)
    put_hud_text(frame, f"{label} {pct}%", (x, y - 6), color, 0.42, 1)


def draw_system_stats(frame, cpu, ram, battery_pct, charging):
    """Bottom-left 'suit diagnostics' panel: CPU / RAM / battery bars,
    like an Iron Man HUD power readout."""
    h, w = frame.shape[:2]
    panel_x = 16
    bar_w = 130
    bar_h = 6
    gap = 26
    base_y = h - 90

    put_hud_text(frame, "SUIT DIAGNOSTICS", (panel_x, base_y - 14), (150, 150, 150), 0.4, 1)
    draw_stat_bar(frame, panel_x, base_y, bar_w, bar_h, cpu, CYAN, "CPU")
    draw_stat_bar(frame, panel_x, base_y + gap, bar_w, bar_h, ram, GOLD, "RAM")

    if battery_pct is not None:
        batt_color = MAGENTA if not charging else CYAN
        label = "PWR (chg)" if charging else "PWR"
        draw_stat_bar(frame, panel_x, base_y + gap * 2, bar_w, bar_h, battery_pct, batt_color, label)


def get_system_stats():
    """Poll CPU/RAM/battery via psutil. Returns
    (cpu_percent, ram_percent, battery_percent_or_None, is_charging).
    Never raises — desktops without a battery just get None for that
    slot."""
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    battery_pct, charging = None, False
    try:
        batt = psutil.sensors_battery()
        if batt is not None:
            battery_pct = batt.percent
            charging = bool(batt.power_plugged)
    except Exception as e:
        print("Battery read failed:", e)
    return cpu, ram, battery_pct, charging


def greeting_for_time():
    """Time-of-day greeting used at BOOT, JARVIS style. "Good night" is
    deliberately not used here — it's a farewell, not a greeting (see
    farewell_for_time below for that)."""
    hour = time.localtime().tm_hour
    if hour < 12:
        return "Good morning"
    elif hour < 17:
        return "Good afternoon"
    else:
        return "Good evening"


def farewell_for_time():
    """Time-aware sign-off used when quitting. "Good night" only fires
    late at night; daytime gets "Have a good day" instead."""
    hour = time.localtime().tm_hour
    if hour >= 21 or hour < 5:
        return "Good night, sir."
    return "Have a good day, sir."


def run_boot_sequence(window_name, width=960, height=540, duration=3.2, person_name=None):
    """JARVIS-style boot animation played once when the script starts:
    rotating arc rings, a pulsing core, a fill progress bar, and status
    lines revealing one by one, with a spoken personalized greeting.
    If person_name is given (from face recognition), it's used in place
    of "sir".

    Every text element is centered using cv2.getTextSize (rather than a
    hardcoded x offset) and the vertical layout is stacked top -> bottom
    with fixed gaps around the ring cluster, so the title / greeting /
    rings / status lines / progress bar never overlap."""
    address = person_name if person_name else "sir"
    greeting = f"{greeting_for_time()}, {address}. Systems online."
    speak(greeting)
    lines = [
        "INITIALIZING NEURAL INTERFACE",
        "CALIBRATING HAND TRACKING MODULE",
        "LOADING AUDIO SUBSYSTEM",
        "J.A.R.V.I.S. ONLINE",
    ]
    per_line = duration / (len(lines) + 1)
    start = time.time()

    cx, cy = width // 2, height // 2
    ring_max_r = 34 + 2 * 26  # outer ring radius (86px)

    title_font, title_scale, title_thick = cv2.FONT_HERSHEY_SIMPLEX, 1.3, 3
    sub_font, sub_scale, sub_thick = cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1

    (title_w, _), _ = cv2.getTextSize("J A R V I S", title_font, title_scale, title_thick)
    (greet_w, _), _ = cv2.getTextSize(greeting.upper(), sub_font, sub_scale, sub_thick)

    # Stacked layout, top to bottom, with clear gaps around the ring
    # cluster so nothing collides: title -> greeting -> [rings] -> lines -> bar.
    title_y = cy - ring_max_r - 90
    greet_y = title_y + 45
    line_start_y = cy + ring_max_r + 40
    line_gap = 24

    bar_w, bar_h = 260, 4
    bar_x = cx - bar_w // 2
    bar_y = line_start_y + line_gap * len(lines) + 16

    while True:
        t = time.time() - start
        if t > duration:
            break

        frame = np.zeros((height, width, 3), dtype=np.uint8)

        spin = (t * 140) % 360
        for i in range(3):
            r = 34 + i * 26
            ang0 = spin + i * 50
            cv2.ellipse(frame, (cx, cy), (r, r), ang0, 0, 260, GOLD, 2, cv2.LINE_AA)
            cv2.ellipse(frame, (cx, cy), (r, r), ang0 + 180, 0, 80, CYAN, 2, cv2.LINE_AA)

        pulse_r = int(14 + 6 * math.sin(t * 8))
        cv2.circle(frame, (cx, cy), pulse_r, WHITE, -1, cv2.LINE_AA)
        cv2.circle(frame, (cx, cy), pulse_r + 6, CYAN, 1, cv2.LINE_AA)

        title_alpha = min(1.0, t / 0.6)
        title_color = tuple(int(c * title_alpha) for c in GOLD)
        cv2.putText(frame, "J A R V I S", (cx - title_w // 2, title_y),
                    title_font, title_scale, title_color, title_thick, cv2.LINE_AA)
        if t > 0.6:
            cv2.putText(frame, greeting.upper(), (cx - greet_w // 2, greet_y),
                        sub_font, sub_scale, WHITE, sub_thick, cv2.LINE_AA)

        y = line_start_y
        for i, line in enumerate(lines):
            if t > i * per_line:
                fade = min(1.0, (t - i * per_line) / 0.3)
                color = tuple(int(c * fade) for c in CYAN)
                (line_w, _), _ = cv2.getTextSize(line, sub_font, sub_scale, sub_thick)
                cv2.putText(frame, line, (cx - line_w // 2, y),
                            sub_font, sub_scale, color, sub_thick, cv2.LINE_AA)
                y += line_gap

        # Progress bar — fills left to right over the whole boot duration.
        progress = min(1.0, t / duration)
        cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h),
                       (60, 60, 60), 1, cv2.LINE_AA)
        cv2.rectangle(frame, (bar_x, bar_y),
                       (bar_x + int(bar_w * progress), bar_y + bar_h), GOLD, -1, cv2.LINE_AA)
        put_hud_text(frame, f"{int(progress * 100)}%",
                     (bar_x + bar_w + 14, bar_y + bar_h + 3), GOLD, 0.45, 1)

        # Small corner flourishes to fill out the HUD.
        put_hud_text(frame, "SYSTEM BOOT", (20, 30), (140, 140, 140), 0.45, 1)
        put_hud_text(frame, "A.B.E. LABS  //  MARK I", (width - 240, height - 20),
                     (140, 140, 140), 0.45, 1)

        draw_scanlines(frame, spacing=3, alpha=0.05)
        draw_corner_brackets(frame, GOLD)
        cv2.imshow(window_name, frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break


# ==================================================================
#  Occasional idle chatter, JARVIS style
# ==================================================================
JARVIS_IDLE_LINES = [
    "All systems nominal",
    "Standing by",
    "Brightness optimal, sir",
    "Awaiting your command",
    "All parameters within range",
]
ONELINER_COOLDOWN = 25     # seconds between idle one-liners

HARD_PINCH_PX = 35         # distance below which we call it a "hard pinch"
IDLE_THRESHOLD = 1.5       # seconds with no hand before STANDBY HUD shows

# Voice cooldown so normal slider changes don't announce every frame
VOICE_COOLDOWN = 1.8       # seconds between routine value announcements
VOICE_STEP = 10            # only announce on >=10% jumps


# Voice-command step size for volume/brightness nudges
VOICE_CMD_STEP = 10


_location_cache = None  # detected once per run, then reused


def get_location():
    """Auto-detect the current city from the machine's IP address.
    Tries ipapi.co first (HTTPS — works through most firewalls/AV),
    then falls back to ip-api.com (HTTP only, which some networks/
    antivirus silently block). Cached after the first successful
    lookup for the rest of the run. Returns a dict with lat/lon/city,
    or None if every provider fails (caller should fall back to
    WEATHER_CITY in that case)."""
    global _location_cache
    if _location_cache is not None:
        return _location_cache

    # ---- Provider 1: ipapi.co (HTTPS, no key needed, ~1000 req/day) ----
    try:
        r = requests.get("https://ipapi.co/json/", timeout=5).json()
        if r.get("latitude") and not r.get("error"):
            _location_cache = {
                "lat": r["latitude"],
                "lon": r["longitude"],
                "city": r.get("city") or WEATHER_CITY,
            }
            return _location_cache
        print("Location provider ipapi.co failed:", r.get("reason", "unknown response"))
    except Exception as e:
        print(f"Location provider ipapi.co failed: {type(e).__name__}: {e}")

    # ---- Provider 2: ip-api.com (HTTP only — fallback) ----
    try:
        r = requests.get("http://ip-api.com/json/", timeout=5).json()
        if r.get("status") == "success":
            _location_cache = {
                "lat": r["lat"],
                "lon": r["lon"],
                "city": r.get("city") or WEATHER_CITY,
            }
            return _location_cache
        print("Location provider ip-api.com failed:", r.get("message", "unknown error"))
    except Exception as e:
        print(f"Location provider ip-api.com failed: {type(e).__name__}: {e}")

    print(f"Location auto-detect failed on all providers — falling back to WEATHER_CITY ({WEATHER_CITY}).")
    return None


def get_weather_report():
    """Fetch current weather + air quality from OpenWeatherMap for the
    auto-detected location and return a spoken-friendly sentence, or
    None if unconfigured / the request fails (never raises — safe to
    call from the main loop)."""
    if not WEATHER_API_KEY:
        print("Weather: set WEATHER_API_KEY in your .env file first (see .env.example).")
        return None

    loc = get_location()
    if loc:
        params = {"lat": loc["lat"], "lon": loc["lon"], "appid": WEATHER_API_KEY, "units": "metric"}
    else:
        # IP geolocation failed — fall back to the fixed WEATHER_CITY.
        params = {"q": WEATHER_CITY, "appid": WEATHER_API_KEY, "units": "metric"}

    try:
        w = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params=params,
            timeout=5,
        ).json()
        if str(w.get("cod")) != "200":
            print("Weather request failed:", w.get("message"))
            return None

        temp = round(w["main"]["temp"])
        feels_like = round(w["main"]["feels_like"])
        desc = w["weather"][0]["description"]
        lat, lon = w["coord"]["lat"], w["coord"]["lon"]
        city_name = w.get("name") or (loc["city"] if loc else WEATHER_CITY)

        # OpenWeatherMap's Air Pollution API reports AQI on its own
        # 1-5 scale (Good/Fair/Moderate/Poor/Very Poor) — NOT the
        # 0-500 US EPA scale some other services use.
        aqi_label = None
        try:
            a = requests.get(
                "https://api.openweathermap.org/data/2.5/air_pollution",
                params={"lat": lat, "lon": lon, "appid": WEATHER_API_KEY},
                timeout=5,
            ).json()
            aqi_index = a["list"][0]["main"]["aqi"]
            aqi_label = {1: "good", 2: "fair", 3: "moderate", 4: "poor", 5: "very poor"}.get(aqi_index)
        except Exception as e:
            print("Air quality request failed:", e)

        report = f"Sir, it's currently {temp} degrees in {city_name}, feels like {feels_like}, with {desc}."
        if aqi_label:
            report += f" Air quality is {aqi_label}."
        return report

    except Exception as e:
        print("Weather fetch failed:", e)
        return None


def voice_command_listener(cmd_queue):
    """Background thread: listens on the default microphone and pushes
    recognized phrases (lowercased text) into cmd_queue for the main
    loop to act on. This only does audio INPUT via PyAudio — it never
    touches SAPI5, so running it as a thread in the main process
    (alongside cv2/mediapipe) is safe."""
    try:
        import speech_recognition as sr
    except ImportError:
        print("Voice commands disabled: pip install SpeechRecognition pyaudio")
        return

    recognizer = sr.Recognizer()
    try:
        mic = sr.Microphone()
    except Exception as e:
        print("Voice commands disabled: no microphone found:", e)
        return

    try:
        with mic as source:
            recognizer.adjust_for_ambient_noise(source, duration=1)
    except Exception as e:
        print("Voice commands disabled: microphone calibration failed:", e)
        return

    print("Voice command mode active. Try: 'volume up', 'mute brightness', "
          "'sync', 'weather', 'shutdown'...")

    while True:
        try:
            with mic as source:
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=4)
            text = recognizer.recognize_google(audio).lower()
            cmd_queue.put(text)
        except sr.WaitTimeoutError:
            continue
        except sr.UnknownValueError:
            continue  # heard something, couldn't parse it — just keep listening
        except Exception as e:
            print("Voice recognition error:", e)
            time.sleep(1)


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


def load_face_recognizer():
    """Train an LBPH face recognizer from face_data/<name>/*.jpg (built
    with enroll_face.py). Returns (recognizer, face_cascade, label_map)
    or (None, None, None) if cv2.face is unavailable (needs
    opencv-contrib-python, not plain opencv-python) or no faces have
    been enrolled yet — callers should just skip face recognition in
    that case, not crash."""
    if not hasattr(cv2, "face"):
        print("Face recognition disabled: cv2.face not found — "
              "install opencv-contrib-python instead of opencv-python.")
        return None, None, None

    if not FACE_DATA_DIR.is_dir():
        print(f"Face recognition disabled: no {FACE_DATA_DIR}/ folder yet "
              f"(run enroll_face.py to create one).")
        return None, None, None

    try:
        face_cascade = get_face_cascade()
    except Exception as e:
        print("Face recognition disabled: couldn't load the Haar cascade:", e)
        return None, None, None

    images, labels, label_map = [], [], {}
    for person_dir in sorted(FACE_DATA_DIR.iterdir()):
        if not person_dir.is_dir():
            continue
        label_id = len(label_map)
        label_map[label_id] = person_dir.name
        for img_path in person_dir.glob("*.jpg"):
            img = cv2.imread(str(img_path), cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            images.append(img)
            labels.append(label_id)

    if not images:
        print(f"Face recognition disabled: {FACE_DATA_DIR}/ has no usable images yet.")
        return None, None, None

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train(images, np.array(labels))
    print(f"Face recognition ready — enrolled: {list(label_map.values())}")
    return recognizer, face_cascade, label_map


def recognize_face(cap, face_cascade, recognizer, label_map, attempts=25, delay=0.2):
    """Grab frames from the camera for a few seconds and try to
    recognize a face. Returns the matched name, or None if no
    confident match within FACE_CONFIDENCE_THRESHOLD (lower LBPH
    distance = better match). Prints each attempt's confidence score
    so the threshold can be tuned if recognition keeps failing."""
    if recognizer is None:
        return None

    print(f"Scanning for a face — look at the camera (up to {attempts * delay:.0f}s)...")
    best_name, best_confidence = None, float("inf")

    for i in range(attempts):
        success, frame = cap.read()
        if not success:
            time.sleep(delay)
            continue
        frame = cv2.flip(frame, 1)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5)
        if len(faces) == 0:
            time.sleep(delay)
            continue

        x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])  # largest face
        label_id, confidence = recognizer.predict(gray[y:y + fh, x:x + fw])
        name = label_map.get(label_id)
        print(f"  attempt {i + 1}: closest match '{name}', confidence={confidence:.1f} "
              f"(need <= {FACE_CONFIDENCE_THRESHOLD} — lower is better)")

        if confidence < best_confidence:
            best_confidence, best_name = confidence, name
        if confidence <= FACE_CONFIDENCE_THRESHOLD:
            print(f"Recognized '{name}' (confidence {confidence:.1f}).")
            return name

        time.sleep(delay)

    if best_name is not None:
        print(f"No confident match — best guess was '{best_name}' at {best_confidence:.1f}, "
              f"above the {FACE_CONFIDENCE_THRESHOLD} threshold. Falling back to 'sir'. "
              f"If this is consistently you, try raising FACE_CONFIDENCE_THRESHOLD a bit.")
    else:
        print("No face detected during the scan window. Falling back to 'sir'.")
    return None


def get_answer(question):
    """Answer any question like a real chatbot, via Groq's free LLM API
    (llama-3.1-8b-instant — fast, free tier, no credit card needed).
    Falls back to a much more limited DuckDuckGo instant-answer lookup
    if GROQ_API_KEY isn't set, so the feature still does *something*
    without an API key configured."""
    if GROQ_API_KEY:
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": GROQ_MODEL,
                    "messages": [
                        {"role": "system", "content": (
                            "You are JARVIS, Tony Stark's witty but concise AI assistant. "
                            "Answer in 1-3 short sentences — your reply gets read aloud by "
                            "text-to-speech, so avoid lists, markdown, or long explanations. "
                            "Address the user as 'sir'."
                        )},
                        {"role": "user", "content": question},
                    ],
                    "max_tokens": 150,
                    "temperature": 0.6,
                },
                timeout=15,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print("Groq Q&A failed, falling back to DuckDuckGo:", e)

    # ---- Fallback: DuckDuckGo Instant Answer (no key, limited coverage) ----
    try:
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": question, "format": "json", "no_html": 1, "skip_disambig": 1},
            timeout=6,
        ).json()
        answer = r.get("AbstractText") or r.get("Answer") or ""
        if not answer and r.get("RelatedTopics"):
            first = r["RelatedTopics"][0]
            if isinstance(first, dict):
                answer = first.get("Text", "")
        if answer:
            return answer if len(answer) < 400 else answer[:400].rsplit(".", 1)[0] + "."
        return "I couldn't find a clear answer for that, sir."
    except Exception as e:
        print("Q&A lookup failed:", e)
        return "I couldn't reach my knowledge base right now, sir."


def get_news_briefing(max_headlines=3):
    """Fetch top headlines from BBC News RSS (no API key needed) and
    return a spoken-friendly briefing, or None on failure."""
    try:
        resp = requests.get("http://feeds.bbci.co.uk/news/rss.xml", timeout=6)
        root = ET.fromstring(resp.content)
        titles = [item.findtext("title") for item in root.findall(".//item")]
        titles = [t for t in titles if t][:max_headlines]
        if not titles:
            return None
        numbered = ". ".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
        return f"Here are today's top headlines, sir. {numbered}."
    except Exception as e:
        print("News fetch failed:", e)
        return None


# ==================================================================
#  Stats tracker — how many conversations/commands JARVIS has handled,
#  broken down by intent, persisted to data/stats.json so counts
#  survive a restart. Thread-safe: record() is called from the voice
#  thread and the FastAPI request thread; snapshot() from either too.
# ==================================================================
STATS_FILE = Path(__file__).resolve().parent / "data" / "stats.json"


class StatsTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.conversations = 0
        self.intent_counts = Counter()
        self.last_intent = None
        self.last_query = None
        self.last_reply = None
        STATS_FILE.parent.mkdir(exist_ok=True)
        self._load()

    def _load(self):
        if STATS_FILE.is_file():
            try:
                data = json.loads(STATS_FILE.read_text())
                self.conversations = data.get("conversations", 0)
                self.intent_counts = Counter(data.get("intent_counts", {}))
            except Exception as e:
                print("Stats load failed, starting fresh:", e)

    def _save(self):
        try:
            STATS_FILE.write_text(json.dumps({
                "conversations": self.conversations,
                "intent_counts": dict(self.intent_counts),
            }, indent=2))
        except Exception as e:
            print("Stats save failed:", e)

    def record(self, intent, query, reply):
        with self._lock:
            self.conversations += 1
            self.intent_counts[intent] += 1
            self.last_intent = intent
            self.last_query = query
            self.last_reply = reply
            self._save()

    def snapshot(self):
        with self._lock:
            return {
                "uptime_seconds": round(time.time() - self.started_at),
                "conversations_answered": self.conversations,
                "intent_breakdown": dict(self.intent_counts),
                "last_intent": self.last_intent,
                "last_query": self.last_query,
                "last_reply": self.last_reply,
            }


stats = StatsTracker()


# ==================================================================
#  FastAPI server (REST + WebSocket) — lets an external frontend
#  drive JARVIS with text commands and watch the same events the
#  gesture/voice pipeline produces, without needing a mic or camera.
#  Runs via uvicorn in a background thread, started from main().
#
#    GET  /health   -> liveness check
#    GET  /stats    -> conversation counts / uptime / intent breakdown
#    POST /command  -> {"text": "..."} drives JARVIS through the same
#                       intent pipeline the voice path uses
#    WS   /ws       -> real-time event stream: wake / heard / reply
# ==================================================================
app = FastAPI(title="JARVIS Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CommandIn(BaseModel):
    text: str


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, payload: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()
_server_loop = None  # the running asyncio loop, captured on startup


def push_event(payload: dict):
    """Call from ANY thread (gesture loop, voice thread) to broadcast
    an event to every connected WebSocket client. No-op until the
    server's asyncio loop has actually started."""
    if _server_loop is None:
        return
    import asyncio
    asyncio.run_coroutine_threadsafe(manager.broadcast(payload), _server_loop)


@app.on_event("startup")
async def _server_startup():
    global _server_loop
    import asyncio
    _server_loop = asyncio.get_running_loop()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/stats")
async def http_stats():
    return stats.snapshot()


@app.post("/command")
async def http_command(cmd: CommandIn):
    intent, reply = execute_text_command(cmd.text)
    event = {"type": "reply", "query": cmd.text, "intent": intent, "reply": reply}
    push_event(event)
    return {"intent": intent, "reply": reply}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # keep-alive; client pings are ignored
    except WebSocketDisconnect:
        manager.disconnect(ws)


def run_api_server():
    """Runs uvicorn in this (background, daemon) thread. Any error here
    (e.g. port already in use) just disables the HTTP/WS API — gestures
    and voice commands keep working without it."""
    try:
        uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT, log_level="warning")
    except Exception as e:
        print(f"API server disabled (couldn't start on {SERVER_HOST}:{SERVER_PORT}):", e)


# ==================================================================
#  Shared command execution — the SAME logic drives spoken voice
#  commands (via voice_cmd_queue in main()) and text commands sent to
#  POST /command, so nothing has to be implemented twice. Gesture-only
#  actions (lock PC, screenshot, app launch) stay in the main loop
#  since they need hand landmarks, not text.
#
#  Returns (intent, reply_text). Every branch also records the
#  exchange in `stats` and speaks the reply, so callers just need to
#  push whatever HUD notification fits the intent.
# ==================================================================
def execute_text_command(text):
    cmd = text.lower().strip()

    if any(word in cmd for word in ("shutdown", "exit", "goodbye")):
        reply = f"Shutting down. {farewell_for_time()}"
        intent = "shutdown"

    elif "weather" in cmd:
        reply = get_weather_report() or "I couldn't fetch the weather right now, sir."
        intent = "weather"

    elif "news" in cmd or "headline" in cmd:
        reply = get_news_briefing() or "I couldn't reach the news right now, sir."
        intent = "news"

    elif "stat" in cmd or ("how many" in cmd and "convers" in cmd):
        s = stats.snapshot()
        reply = f"I've handled {s['conversations_answered']} conversations so far, sir."
        intent = "stats"

    elif "time" in cmd:
        reply = f"It's {time.strftime('%I:%M %p')}, sir."
        intent = "time"

    elif "date" in cmd or "day is it" in cmd:
        reply = f"Today is {time.strftime('%A, %B %d')}, sir."
        intent = "date"

    elif "who are you" in cmd or "what are you" in cmd:
        reply = "I am JARVIS, your personal assistant, sir."
        intent = "identity"

    elif cmd.startswith("jarvis"):
        question = cmd.split("jarvis", 1)[-1].strip(" ,.")
        if not question:
            reply, intent = "Yes, sir?", "identity"
        else:
            reply, intent = get_answer(question), "chat"

    else:
        reply, intent = get_answer(cmd), "chat"

    stats.record(intent, text, reply)
    return intent, reply


# ==================================================================
#  Hold-to-confirm gesture actions: lock PC, launch an app, screenshot
# ==================================================================
def lock_pc():
    """Lock the Windows session via the user32 API. No confirmation
    dialog — the target-lock hold animation IS the confirmation."""
    try:
        ctypes.windll.user32.LockWorkStation()
        return True
    except Exception as e:
        print("Lock PC failed:", e)
        return False


def launch_chrome():
    """Launch Chrome. Tries the two common install paths first, then
    falls back to the OS's own file-association lookup for "chrome"."""
    for p in CHROME_PATHS:
        if Path(p).is_file():
            try:
                subprocess.Popen([p])
                return True
            except Exception as e:
                print("Chrome launch failed:", e)
    try:
        os.startfile("chrome")
        return True
    except Exception as e:
        print("Chrome launch failed (no known install path found either):", e)
        return False


def launch_spotify():
    """Launch Spotify. Tries the spotify: URI protocol first (opens the
    installed desktop app directly if it's registered as the handler),
    then falls back to the default per-user install location."""
    try:
        os.startfile("spotify:")
        return True
    except Exception as e:
        print("Spotify launch via protocol failed, trying install path:", e)

    spotify_exe = Path(os.environ.get("APPDATA", "")) / "Spotify" / "Spotify.exe"
    if spotify_exe.is_file():
        try:
            subprocess.Popen([str(spotify_exe)])
            return True
        except Exception as e:
            print("Spotify launch failed:", e)
    return False


def take_screenshot():
    """Grab the full desktop with PIL and save it with a timestamped
    filename under SCREENSHOT_DIR. Returns True on success."""
    try:
        SCREENSHOT_DIR.mkdir(exist_ok=True)
        filename = SCREENSHOT_DIR / f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}.png"
        img = ImageGrab.grab()
        img.save(filename)
        print(f"Screenshot saved to {filename}")
        return True
    except Exception as e:
        print("Screenshot failed:", e)
        return False


def main():
    global _tts_queue

    # ---------- Start the dedicated TTS process ----------
    # multiprocessing.Queue, not queue.Queue — this crosses a real
    # process boundary, isolated from cv2/mediapipe entirely.
    _tts_queue = multiprocessing.Queue(maxsize=8)
    tts_process = multiprocessing.Process(
        target=_tts_worker_process, args=(_tts_queue,), daemon=True
    )
    tts_process.start()

    # ---------- Start the voice-command listener ----------
    voice_cmd_queue = queue.Queue()
    threading.Thread(
        target=voice_command_listener, args=(voice_cmd_queue,), daemon=True
    ).start()

    # ---------- Start the local REST + WebSocket API server ----------
    # Lets a frontend hit /health, /stats, /command and /ws on
    # SERVER_HOST:SERVER_PORT even without a mic/camera driving things.
    threading.Thread(target=run_api_server, daemon=True).start()
    print(f"API server starting on http://{SERVER_HOST}:{SERVER_PORT} "
          f"(/health, /stats, /command, /ws)")

    # ---------- Hand tracking model (loaded once, here) ----------
    hands = mp_hands.Hands(
        max_num_hands=2,
        min_detection_confidence=0.7,
        min_tracking_confidence=0.7,
    )

    # ---------- System volume setup (Windows) ----------
    audio_device = AudioUtilities.GetSpeakers()
    volume_ctrl = audio_device.EndpointVolume

    # ---------- Camera setup ----------
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)

    # ---------- Face recognition (optional — greets you by name) ----------
    recognizer, face_cascade, label_map = load_face_recognizer()
    recognized_name = recognize_face(cap, face_cascade, recognizer, label_map)

    # ---------- Runtime state ----------
    brightness = 0
    last_sent_brightness = -1
    last_sent_volume = -1

    # Edge-trigger trackers so we mute / sync / speak once per gesture,
    # not every single frame while the pose is held.
    right_fist_prev = False   # brightness-hand fist state last frame
    left_fist_prev = False    # volume-hand fist state last frame
    sync_prev = False         # both-hands-hard-pinch state last frame

    last_voice_time = {"volume": 0.0, "brightness": 0.0}
    last_announced = {"volume": None, "brightness": None}

    last_hand_time = time.time()
    last_oneliner_time = 0.0
    quit_requested = False

    # ---------- Hold-to-confirm gesture state ----------
    lock_hold_start = None
    screenshot_hold_start = None
    chrome_hold_start = None
    spotify_hold_start = None
    last_lock_time = 0.0
    last_screenshot_time = 0.0
    last_chrome_time = 0.0
    last_spotify_time = 0.0
    screenshot_flash_until = 0.0

    # ---------- Live system stats (CPU/RAM/battery) ----------
    cpu_stat, ram_stat, battery_stat, charging_stat = get_system_stats()
    last_stats_poll = time.time()

    # Play the JARVIS boot animation once, before the camera loop starts.
    run_boot_sequence("Gesture Control - HUD", person_name=recognized_name)

    # Announce the weather right after boot, if configured.
    boot_weather = get_weather_report()
    if boot_weather:
        speak(boot_weather)

    start_time = time.time()

    while True:
        success, frame = cap.read()
        if not success:
            break

        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = hands.process(rgb)

        h, w, _ = frame.shape
        spin_angle = (time.time() - start_time) * 60 % 360

        draw_hud_grid(frame)

        hands_this_frame = []  # (hand_landmarks, side, mid_point, dist, fist, ...)

        if result.multi_hand_landmarks:
            for hand_landmarks in result.multi_hand_landmarks:
                wrist = hand_landmarks.landmark[WRIST]
                hand_x = wrist.x * w
                dist = pinch_distance(hand_landmarks, w, h)

                l_shape = is_l_shape(hand_landmarks)
                peace = is_peace_sign(hand_landmarks)
                open_palm = is_open_palm(hand_landmarks)
                # is_fist and is_l_shape can both fire on the same pose
                # (three curled fingers) — l-shape takes priority so a
                # LOCK gesture never gets swallowed as a plain mute.
                fist = is_fist(hand_landmarks) and not l_shape

                thumb = hand_landmarks.landmark[THUMB_TIP]
                index = hand_landmarks.landmark[INDEX_TIP]
                p1 = (int(thumb.x * w), int(thumb.y * h))
                p2 = (int(index.x * w), int(index.y * h))
                mid = ((p1[0] + p2[0]) // 2, (p1[1] + p2[1]) // 2)

                side = "right" if hand_x >= w / 2 else "left"
                hands_this_frame.append({
                    "landmarks": hand_landmarks, "side": side, "mid": mid,
                    "p1": p1, "p2": p2, "dist": dist, "fist": fist,
                    "l_shape": l_shape, "peace": peace, "open_palm": open_palm,
                })

        if hands_this_frame:
            last_hand_time = time.time()

        now = time.time()

        # ---------------- Two-hand SYNC gesture ----------------
        sync_now = False
        if len(hands_this_frame) == 2:
            d0 = hands_this_frame[0]["dist"]
            d1 = hands_this_frame[1]["dist"]
            if d0 < HARD_PINCH_PX and d1 < HARD_PINCH_PX:
                sync_now = True

        if sync_now and not sync_prev:
            avg_dist = (hands_this_frame[0]["dist"] + hands_this_frame[1]["dist"]) / 2
            sync_percent = int(np.clip(np.interp(avg_dist, [20, 200], [0, 100]), 0, 100))
            brightness = sync_percent
            try:
                sbc.set_brightness(sync_percent)
                last_sent_brightness = sync_percent
            except Exception as e:
                print("Brightness control failed:", e)
            try:
                volume_ctrl.SetMasterVolumeLevelScalar(sync_percent / 100.0, None)
                last_sent_volume = sync_percent
            except Exception as e:
                print("Volume control failed:", e)

            add_notification(f"SYNC LOCK  {sync_percent}%", MAGENTA, duration=1.8)
            speak(f"Synced at {sync_percent} percent")
            beep(1300, 140)

        sync_prev = sync_now

        # ---------------- Two-hand app-launch gestures ----------------
        # Two open palms held together -> Chrome. Two peace signs held
        # together -> Spotify. Both reuse the target-lock scan animation
        # for the "hold to confirm" feedback.
        combo_active = False
        both_open_palm = len(hands_this_frame) == 2 and all(hd["open_palm"] for hd in hands_this_frame)
        both_peace = len(hands_this_frame) == 2 and all(hd["peace"] for hd in hands_this_frame)

        if both_open_palm:
            combo_active = True
            if chrome_hold_start is None:
                chrome_hold_start = now
            progress = min(1.0, (now - chrome_hold_start) / APP_LAUNCH_HOLD_SECONDS)
            center = ((hands_this_frame[0]["mid"][0] + hands_this_frame[1]["mid"][0]) // 2,
                      (hands_this_frame[0]["mid"][1] + hands_this_frame[1]["mid"][1]) // 2)
            draw_target_lock(frame, center, progress, GOLD, "LAUNCH CHROME")
            if progress >= 1.0 and now - last_chrome_time > ACTION_COOLDOWN:
                if launch_chrome():
                    add_notification("LAUNCHING CHROME", GOLD, duration=1.8)
                    speak("Launching Chrome, sir")
                    beep(1200, 150)
                last_chrome_time = now
                chrome_hold_start = None
        else:
            chrome_hold_start = None

        if both_peace:
            combo_active = True
            if spotify_hold_start is None:
                spotify_hold_start = now
            progress = min(1.0, (now - spotify_hold_start) / APP_LAUNCH_HOLD_SECONDS)
            center = ((hands_this_frame[0]["mid"][0] + hands_this_frame[1]["mid"][0]) // 2,
                      (hands_this_frame[0]["mid"][1] + hands_this_frame[1]["mid"][1]) // 2)
            draw_target_lock(frame, center, progress, GREEN, "LAUNCH SPOTIFY")
            if progress >= 1.0 and now - last_spotify_time > ACTION_COOLDOWN:
                if launch_spotify():
                    add_notification("LAUNCHING SPOTIFY", GREEN, duration=1.8)
                    speak("Launching Spotify, sir")
                    beep(1200, 150)
                last_spotify_time = now
                spotify_hold_start = None
        else:
            spotify_hold_start = None

        # Reset the single-hand hold timers whenever the pose isn't
        # currently held by any hand (or got absorbed into a two-hand combo).
        lock_active_now = any(hd["l_shape"] for hd in hands_this_frame) and not combo_active
        screenshot_active_now = any(hd["peace"] for hd in hands_this_frame) and not both_peace and not combo_active
        if not lock_active_now:
            lock_hold_start = None
        if not screenshot_active_now:
            screenshot_hold_start = None

        # ---------------- Per-hand control ----------------
        for hd in hands_this_frame:
            if combo_active:
                # Both hands are mid-way through a two-hand app-launch
                # gesture this frame — skip normal slider/mute handling.
                continue

            side = hd["side"]
            mid = hd["mid"]
            p1, p2 = hd["p1"], hd["p2"]
            dist = hd["dist"]
            fist = hd["fist"]

            # -------- LOCK PC (L-shape, one hand) --------
            if hd["l_shape"]:
                if lock_hold_start is None:
                    lock_hold_start = now
                progress = min(1.0, (now - lock_hold_start) / LOCK_HOLD_SECONDS)
                draw_target_lock(frame, mid, progress, RED, "LOCKING PC")
                if progress >= 1.0 and now - last_lock_time > ACTION_COOLDOWN:
                    speak("Locking the system now, sir")
                    add_notification("SYSTEM LOCKED", RED, duration=1.8)
                    beep(600, 200)
                    lock_pc()
                    last_lock_time = now
                    lock_hold_start = None
                cv2.line(frame, p1, p2, WHITE, 1, cv2.LINE_AA)
                continue

            # -------- SCREENSHOT (peace sign, one hand) --------
            if hd["peace"] and not both_peace:
                if screenshot_hold_start is None:
                    screenshot_hold_start = now
                progress = min(1.0, (now - screenshot_hold_start) / SCREENSHOT_HOLD_SECONDS)
                draw_target_lock(frame, mid, progress, CYAN, "CAPTURING")
                if progress >= 1.0 and now - last_screenshot_time > ACTION_COOLDOWN:
                    if take_screenshot():
                        screenshot_flash_until = now + SCREENSHOT_FLASH_SECONDS
                        shutter_sound()
                        add_notification("SCREENSHOT SAVED", CYAN, duration=1.6)
                    last_screenshot_time = now
                    screenshot_hold_start = None
                cv2.line(frame, p1, p2, WHITE, 1, cv2.LINE_AA)
                continue

            if side == "right":
                # -------- Brightness (gold) --------
                if fist:
                    if not right_fist_prev:
                        brightness = 0
                        try:
                            sbc.set_brightness(0)
                            last_sent_brightness = 0
                        except Exception as e:
                            print("Brightness control failed:", e)
                        add_notification("BRIGHTNESS MUTED", RED)
                        speak("Brightness muted")
                        beep(350, 130)
                    draw_glow_circle(frame, mid, 46, RED)
                    draw_arc_ring(frame, mid, 46, 0, GOLD, spin_angle, muted=True)
                    put_hud_text(frame, "MUTED", (mid[0] - 24, mid[1] + 70), RED)
                else:
                    if not sync_now:
                        brightness = int(np.clip(np.interp(dist, [20, 200], [0, 100]), 0, 100))
                        if abs(brightness - last_sent_brightness) >= 3:
                            try:
                                sbc.set_brightness(brightness)
                                last_sent_brightness = brightness
                            except Exception as e:
                                print("Brightness control failed:", e)

                            now_t = time.time()
                            prev = last_announced["brightness"]
                            if (prev is None or abs(brightness - prev) >= VOICE_STEP) and \
                                    now_t - last_voice_time["brightness"] > VOICE_COOLDOWN:
                                speak(f"Brightness set to {brightness} percent")
                                last_voice_time["brightness"] = now_t
                                last_announced["brightness"] = brightness
                                add_notification(f"BRIGHTNESS  {brightness}%", GOLD)
                                beep(900, 70)

                    draw_glow_circle(frame, mid, 46, GOLD)
                    draw_arc_ring(frame, mid, 46, brightness, GOLD, spin_angle)
                    put_hud_text(frame, f"{brightness}%", (mid[0] - 16, mid[1] + 70), GOLD)

                right_fist_prev = fist

            else:
                # -------- Volume (cyan) --------
                if fist:
                    if not left_fist_prev:
                        try:
                            volume_ctrl.SetMasterVolumeLevelScalar(0.0, None)
                            last_sent_volume = 0
                        except Exception as e:
                            print("Volume control failed:", e)
                        add_notification("VOLUME MUTED", RED)
                        speak("Volume muted")
                        beep(350, 130)
                    draw_glow_circle(frame, mid, 46, RED)
                    draw_arc_ring(frame, mid, 46, 0, CYAN, -spin_angle, muted=True)
                    put_hud_text(frame, "MUTED", (mid[0] - 24, mid[1] + 70), RED)
                else:
                    if not sync_now:
                        vol_percent = int(np.clip(np.interp(dist, [20, 200], [0, 100]), 0, 100))
                        if abs(vol_percent - last_sent_volume) >= 2:
                            try:
                                volume_ctrl.SetMasterVolumeLevelScalar(vol_percent / 100.0, None)
                                last_sent_volume = vol_percent
                            except Exception as e:
                                print("Volume control failed:", e)

                            now_t = time.time()
                            prev = last_announced["volume"]
                            if (prev is None or abs(vol_percent - prev) >= VOICE_STEP) and \
                                    now_t - last_voice_time["volume"] > VOICE_COOLDOWN:
                                speak(f"Volume set to {vol_percent} percent")
                                last_voice_time["volume"] = now_t
                                last_announced["volume"] = vol_percent
                                add_notification(f"VOLUME  {vol_percent}%", CYAN)
                                beep(900, 70)
                    else:
                        vol_percent = last_sent_volume if last_sent_volume >= 0 else 0

                    draw_glow_circle(frame, mid, 46, CYAN)
                    draw_arc_ring(frame, mid, 46, vol_percent if not fist else 0, CYAN, -spin_angle)
                    put_hud_text(frame, f"{vol_percent}%", (mid[0] - 16, mid[1] + 70), CYAN)

                left_fist_prev = fist

            cv2.line(frame, p1, p2, WHITE, 1, cv2.LINE_AA)

        # ---------------- Idle standby HUD ----------------
        idle_for = time.time() - last_hand_time
        if not hands_this_frame and idle_for > IDLE_THRESHOLD:
            draw_standby(frame, time.time() - start_time)

            # Occasional random JARVIS one-liner while sitting idle
            now_t = time.time()
            if now_t - last_oneliner_time > ONELINER_COOLDOWN:
                line = random.choice(JARVIS_IDLE_LINES)
                speak(line)
                add_notification(line.upper(), CYAN, duration=2.0)
                last_oneliner_time = now_t

        # ---------------- Live system stats (throttled poll) ----------------
        if time.time() - last_stats_poll > STATS_REFRESH_INTERVAL:
            cpu_stat, ram_stat, battery_stat, charging_stat = get_system_stats()
            last_stats_poll = time.time()
        draw_system_stats(frame, cpu_stat, ram_stat, battery_stat, charging_stat)


        # ---------------- Voice command processing ----------------
        while not voice_cmd_queue.empty():
            cmd = voice_cmd_queue.get_nowait()
            print("Heard:", cmd)
            push_event({"type": "heard", "text": cmd})

            hardware_cmd = ("sync" in cmd or ("mute" in cmd and ("bright" in cmd or "volume" in cmd))
                             or ("bright" in cmd and ("up" in cmd or "down" in cmd))
                             or ("volume" in cmd and ("up" in cmd or "down" in cmd)))

            if any(word in cmd for word in ("shutdown", "exit", "goodbye")):
                quit_requested = True
                stats.record("shutdown", cmd, "shutting down")

            elif not hardware_cmd:
                # Everything that ISN'T a hardware slider/mute command
                # (weather, news, stats, time, date, identity, "jarvis
                # <question>", general chat) goes through the same
                # execute_text_command() the HTTP /command endpoint
                # uses, so voice and text stay in sync and get counted
                # in `stats` the same way.
                intent, reply = execute_text_command(cmd)
                notif_colors = {"weather": CYAN, "news": CYAN, "stats": CYAN, "chat": CYAN}
                notif_labels = {"weather": "WEATHER CHECK", "news": "NEWS BRIEFING",
                                 "stats": "STATS", "chat": "ANSWERING..."}
                if intent in notif_labels:
                    add_notification(notif_labels[intent], notif_colors[intent], duration=1.6)
                speak(reply)
                push_event({"type": "reply", "query": cmd, "intent": intent, "reply": reply})

            elif "sync" in cmd:
                level = max(last_sent_volume, last_sent_brightness, 0)
                brightness = level
                try:
                    sbc.set_brightness(level)
                    last_sent_brightness = level
                except Exception as e:
                    print("Brightness control failed:", e)
                try:
                    volume_ctrl.SetMasterVolumeLevelScalar(level / 100.0, None)
                    last_sent_volume = level
                except Exception as e:
                    print("Volume control failed:", e)
                add_notification(f"SYNC LOCK  {level}%", MAGENTA, duration=1.8)
                speak(f"Synced at {level} percent")
                beep(1300, 140)
                stats.record("sync", cmd, f"synced at {level} percent")
                push_event({"type": "reply", "query": cmd, "intent": "sync", "reply": f"Synced at {level} percent"})

            elif "mute" in cmd and "bright" in cmd:
                brightness = 0
                try:
                    sbc.set_brightness(0)
                    last_sent_brightness = 0
                except Exception as e:
                    print("Brightness control failed:", e)
                add_notification("BRIGHTNESS MUTED", RED)
                speak("Brightness muted")
                beep(350, 130)
                stats.record("mute_brightness", cmd, "brightness muted")

            elif "mute" in cmd and "volume" in cmd:
                try:
                    volume_ctrl.SetMasterVolumeLevelScalar(0.0, None)
                    last_sent_volume = 0
                except Exception as e:
                    print("Volume control failed:", e)
                add_notification("VOLUME MUTED", RED)
                speak("Volume muted")
                beep(350, 130)
                stats.record("mute_volume", cmd, "volume muted")

            elif "bright" in cmd and "up" in cmd:
                brightness = int(np.clip(brightness + VOICE_CMD_STEP, 0, 100))
                try:
                    sbc.set_brightness(brightness)
                    last_sent_brightness = brightness
                except Exception as e:
                    print("Brightness control failed:", e)
                speak(f"Brightness set to {brightness} percent")
                add_notification(f"BRIGHTNESS  {brightness}%", GOLD)
                beep(900, 70)
                stats.record("brightness", cmd, f"brightness set to {brightness} percent")

            elif "bright" in cmd and "down" in cmd:
                brightness = int(np.clip(brightness - VOICE_CMD_STEP, 0, 100))
                try:
                    sbc.set_brightness(brightness)
                    last_sent_brightness = brightness
                except Exception as e:
                    print("Brightness control failed:", e)
                speak(f"Brightness set to {brightness} percent")
                add_notification(f"BRIGHTNESS  {brightness}%", GOLD)
                beep(900, 70)
                stats.record("brightness", cmd, f"brightness set to {brightness} percent")

            elif "volume" in cmd and "up" in cmd:
                base = last_sent_volume if last_sent_volume >= 0 else 0
                new_vol = int(np.clip(base + VOICE_CMD_STEP, 0, 100))
                try:
                    volume_ctrl.SetMasterVolumeLevelScalar(new_vol / 100.0, None)
                    last_sent_volume = new_vol
                except Exception as e:
                    print("Volume control failed:", e)
                speak(f"Volume set to {new_vol} percent")
                add_notification(f"VOLUME  {new_vol}%", CYAN)
                beep(900, 70)
                stats.record("volume", cmd, f"volume set to {new_vol} percent")

            elif "volume" in cmd and "down" in cmd:
                base = last_sent_volume if last_sent_volume >= 0 else 0
                new_vol = int(np.clip(base - VOICE_CMD_STEP, 0, 100))
                try:
                    volume_ctrl.SetMasterVolumeLevelScalar(new_vol / 100.0, None)
                    last_sent_volume = new_vol
                except Exception as e:
                    print("Volume control failed:", e)
                speak(f"Volume set to {new_vol} percent")
                add_notification(f"VOLUME  {new_vol}%", CYAN)
                beep(900, 70)
                stats.record("volume", cmd, f"volume set to {new_vol} percent")


        # ---------------- Static HUD chrome ----------------
        cv2.line(frame, (w // 2, 0), (w // 2, h), (80, 80, 80), 1)
        put_hud_text(frame, "q: quit   w: weather   n: news", (10, 30), (200, 200, 200))
        put_hud_text(frame, "L-shape: lock PC | peace: screenshot | 2x palm: Chrome | 2x peace: Spotify",
                     (10, 45), (170, 170, 170), 0.42, 1)
        put_hud_text(frame, "VOLUME  (left)", (10, 65), CYAN)
        put_hud_text(frame, "BRIGHTNESS  (right)", (w - 230, 65), GOLD)
        if sync_now:
            put_hud_text(frame, "SYNC LOCK ACTIVE", (w // 2 - 90, h - 20), MAGENTA, 0.6, 2)

        draw_corner_brackets(frame, MAGENTA if sync_now else GOLD)
        draw_notifications(frame)

        # ---------------- Screenshot camera-shutter flash ----------------
        if time.time() < screenshot_flash_until:
            remaining = screenshot_flash_until - time.time()
            alpha = max(0.0, min(1.0, remaining / SCREENSHOT_FLASH_SECONDS))
            white = np.full_like(frame, 255)
            cv2.addWeighted(white, alpha, frame, 1 - alpha, 0, frame)

        cv2.imshow("Gesture Control - HUD", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            quit_requested = True
        elif key == ord("w"):
            report = get_weather_report()
            if report:
                speak(report)
                add_notification("WEATHER CHECK", CYAN, duration=1.6)
        elif key == ord("n"):
            briefing = get_news_briefing()
            if briefing:
                speak(briefing)
                add_notification("NEWS BRIEFING", CYAN, duration=1.6)

        if quit_requested:
            break

    speak(f"Shutting down. {farewell_for_time()}")
    time.sleep(2.5)  # let the farewell finish playing before teardown
    _tts_queue.put(None)  # ask the TTS process to stop cleanly
    tts_process.join(timeout=3)
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    # Required on Windows: multiprocessing re-imports this module in
    # the child process, so anything with side effects (camera, model
    # loading, the TTS process itself) must live behind this guard —
    # otherwise the child would try to open the webcam a second time.
    multiprocessing.freeze_support()
    main()