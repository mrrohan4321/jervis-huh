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

Feedback:
  - Animated arc-reactor style ring (fills with %, rotating tick marks)
  - Fading on-screen notification badges (like the native OS volume popup)
  - Spoken voice feedback via pyttsx3, running in its OWN OS process
    (see _tts_worker_process below) so OpenCV/MediaPipe running in the
    main process can never interfere with the SAPI5 COM apartment.

Voice commands (spoken, not typed — needs a working microphone):
  "volume up" / "volume down"        -> nudge system volume by 10%
  "brightness up" / "brightness down"-> nudge screen brightness by 10%
  "mute volume" / "mute brightness"  -> mute that channel
  "sync"                             -> lock volume + brightness together
  "weather"                          -> speak current weather + air quality
  "shutdown" / "exit" / "goodbye"    -> quit the program

Keyboard controls:
  q  -> quit (speaks a farewell first)
  w  -> manually announce current weather + air quality

Requires (on top of opencv/mediapipe/pycaw/screen_brightness_control):
  pip install pyttsx3 pywin32 SpeechRecognition pyaudio requests
  (if `pip install pyaudio` fails on Windows, try `pip install pipwin`
  then `pipwin install pyaudio`)
"""

import math
import multiprocessing
import queue
import random
import threading
import time
import winsound

import cv2
import numpy as np
import mediapipe as mp
import requests

# Windows system volume control
from pycaw.pycaw import AudioUtilities

# Windows screen brightness control
import screen_brightness_control as sbc

# ---------- Weather + air quality config (OpenWeatherMap) ----------
# Free API key: https://openweathermap.org/api — sign up, then paste
# your key below. Leave WEATHER_CITY as whatever city you want reported.
WEATHER_API_KEY = "YOUR_OPENWEATHERMAP_API_KEY"
WEATHER_CITY = "Kolkata"

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
WRIST = mp_hands.HandLandmark.WRIST

# ---------- HUD colors (BGR) ----------
GOLD = (30, 190, 255)     # brightness
CYAN = (255, 220, 40)     # volume
WHITE = (255, 255, 255)
RED = (60, 60, 255)       # mute
MAGENTA = (255, 60, 220)  # sync

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


def greeting_for_time():
    """Time-of-day greeting, JARVIS style."""
    hour = time.localtime().tm_hour
    if hour < 12:
        return "Good morning"
    elif hour < 17:
        return "Good afternoon"
    else:
        return "Good evening"


def run_boot_sequence(window_name, width=960, height=540, duration=3.2):
    """JARVIS-style boot animation played once when the script starts:
    rotating arc rings, a pulsing core, and status lines revealing one
    by one, with a spoken personalized greeting."""
    greeting = f"{greeting_for_time()}, sir. Systems online."
    speak(greeting)
    lines = [
        "INITIALIZING NEURAL INTERFACE",
        "CALIBRATING HAND TRACKING MODULE",
        "LOADING AUDIO SUBSYSTEM",
        "J.A.R.V.I.S. ONLINE",
    ]
    per_line = duration / (len(lines) + 1)
    start = time.time()

    while True:
        t = time.time() - start
        if t > duration:
            break

        frame = np.zeros((height, width, 3), dtype=np.uint8)
        cx, cy = width // 2, height // 2

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
        cv2.putText(frame, "J A R V I S", (cx - 150, cy - 110),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.3, title_color, 3, cv2.LINE_AA)
        if t > 0.6:
            cv2.putText(frame, greeting.upper(), (cx - 190, cy - 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, WHITE, 1, cv2.LINE_AA)

        y = cy + 110
        for i, line in enumerate(lines):
            if t > i * per_line:
                fade = min(1.0, (t - i * per_line) / 0.3)
                color = tuple(int(c * fade) for c in CYAN)
                cv2.putText(frame, line, (cx - 190, y),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
                y += 26

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


def get_weather_report():
    """Fetch current weather + air quality from OpenWeatherMap and
    return a spoken-friendly sentence, or None if unconfigured / the
    request fails (never raises — safe to call from the main loop)."""
    if not WEATHER_API_KEY or WEATHER_API_KEY == "YOUR_OPENWEATHERMAP_API_KEY":
        print("Weather: set WEATHER_API_KEY near the top of the script first.")
        return None

    try:
        w = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": WEATHER_CITY, "appid": WEATHER_API_KEY, "units": "metric"},
            timeout=5,
        ).json()
        if str(w.get("cod")) != "200":
            print("Weather request failed:", w.get("message"))
            return None

        temp = round(w["main"]["temp"])
        feels_like = round(w["main"]["feels_like"])
        desc = w["weather"][0]["description"]
        lat, lon = w["coord"]["lat"], w["coord"]["lon"]

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

        report = f"Sir, it's currently {temp} degrees, feels like {feels_like}, with {desc}."
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

    # Play the JARVIS boot animation once, before the camera loop starts.
    run_boot_sequence("Gesture Control - HUD")

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

        hands_this_frame = []  # (hand_landmarks, side, mid_point, dist, fist)

        if result.multi_hand_landmarks:
            for hand_landmarks in result.multi_hand_landmarks:
                wrist = hand_landmarks.landmark[WRIST]
                hand_x = wrist.x * w
                dist = pinch_distance(hand_landmarks, w, h)
                fist = is_fist(hand_landmarks)

                thumb = hand_landmarks.landmark[THUMB_TIP]
                index = hand_landmarks.landmark[INDEX_TIP]
                p1 = (int(thumb.x * w), int(thumb.y * h))
                p2 = (int(index.x * w), int(index.y * h))
                mid = ((p1[0] + p2[0]) // 2, (p1[1] + p2[1]) // 2)

                side = "right" if hand_x >= w / 2 else "left"
                hands_this_frame.append({
                    "landmarks": hand_landmarks, "side": side, "mid": mid,
                    "p1": p1, "p2": p2, "dist": dist, "fist": fist,
                })

        if hands_this_frame:
            last_hand_time = time.time()

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

        # ---------------- Per-hand control ----------------
        for hd in hands_this_frame:
            side = hd["side"]
            mid = hd["mid"]
            p1, p2 = hd["p1"], hd["p2"]
            dist = hd["dist"]
            fist = hd["fist"]

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

        # ---------------- Voice command processing ----------------
        while not voice_cmd_queue.empty():
            cmd = voice_cmd_queue.get_nowait()
            print("Heard:", cmd)

            if any(word in cmd for word in ("shutdown", "exit", "goodbye")):
                quit_requested = True

            elif "weather" in cmd:
                report = get_weather_report()
                if report:
                    speak(report)
                    add_notification("WEATHER CHECK", CYAN, duration=1.6)

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

            elif "mute" in cmd and "volume" in cmd:
                try:
                    volume_ctrl.SetMasterVolumeLevelScalar(0.0, None)
                    last_sent_volume = 0
                except Exception as e:
                    print("Volume control failed:", e)
                add_notification("VOLUME MUTED", RED)
                speak("Volume muted")
                beep(350, 130)

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

        # ---------------- Static HUD chrome ----------------
        cv2.line(frame, (w // 2, 0), (w // 2, h), (80, 80, 80), 1)
        put_hud_text(frame, "q: quit   w: weather", (10, 30), (200, 200, 200))
        put_hud_text(frame, "VOLUME  (left)", (10, 55), CYAN)
        put_hud_text(frame, "BRIGHTNESS  (right)", (w - 230, 55), GOLD)
        if sync_now:
            put_hud_text(frame, "SYNC LOCK ACTIVE", (w // 2 - 90, h - 20), MAGENTA, 0.6, 2)

        draw_corner_brackets(frame, MAGENTA if sync_now else GOLD)
        draw_notifications(frame)

        cv2.imshow("Gesture Control - HUD", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            quit_requested = True
        elif key == ord("w"):
            report = get_weather_report()
            if report:
                speak(report)
                add_notification("WEATHER CHECK", CYAN, duration=1.6)

        if quit_requested:
            break

    speak("Shutting down, sir. Goodbye.")
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