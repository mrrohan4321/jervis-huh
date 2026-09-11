# JARVIS (JERVIS.py)

Webcam gesture control (brightness/volume) + voice commands + a local
REST/WebSocket API, all in one script — Iron Man/JARVIS-style HUD.

## What's in JERVIS.py

- Hand-gesture brightness/volume control, lock PC, screenshot, launch
  Chrome/Spotify (see the module docstring at the top of the file for
  the full gesture list).
- Voice commands via your mic (Google Speech Recognition).
- Weather + air quality, news headlines, and "ask JARVIS anything"
  (Groq LLM, with a DuckDuckGo fallback if no key is set).
- Face-recognition boot greeting (`enroll_face.py` + `face_data/`).
- A local FastAPI server (REST + WebSocket) so a separate frontend can
  drive JARVIS with text and watch the same events voice produces —
  no mic or camera required for that path.
- Conversation stats (how many commands answered, broken down by
  intent), persisted to `data/stats.json`.

## Setup

1. **Install dependencies**
   ```
   pip install -r requirements.txt
   ```
   (If `pip install pyaudio` fails on Windows: `pip install pipwin`
   then `pipwin install pyaudio`.)

   Face recognition needs **opencv-contrib-python** specifically
   (`cv2.face` isn't in plain `opencv-python`):
   ```
   pip uninstall opencv-python
   pip install opencv-contrib-python
   ```

2. **Configure secrets**
   ```
   cp .env.example .env
   ```
   Fill in your own `GROQ_API_KEY` (free at console.groq.com) and
   `WEATHER_API_KEY` (free at openweathermap.org) in `.env`.
   **Never commit `.env`** — it's already in `.gitignore`.

   > If you were reusing keys from an older copy of this script —
   > including the ones that used to be hardcoded directly in
   > `JERVIS.py` — **rotate them now**. Any key that was ever committed
   > to a file in plaintext should be treated as compromised, even if
   > you delete it afterward.

3. **(Optional) Enroll your face**
   ```
   py -3.11 enroll_face.py
   ```
   Follow the prompts — no enrollment needed, JARVIS just falls back
   to "sir" if `face_data/` is empty.

4. **Run it**
   ```
   py -3.11 JERVIS.py
   ```

## Local API (new)

While `JERVIS.py` is running, it also serves an HTTP/WebSocket API on
`SERVER_HOST:SERVER_PORT` (default `0.0.0.0:8000`, configurable in
`.env`):

- `GET  /health`  — liveness check
- `GET  /stats`   — `{ uptime_seconds, conversations_answered, intent_breakdown, ... }`
- `GET  /history` — `{ count, entries: [...] }`, the full transcript
  (every query + reply, not just counts) read back from
  `data/transcript.log`. Survives a restart, unlike the short
  in-memory follow-up window `conversation.py` keeps for the LLM.
- `POST /command` — body `{ "text": "what's the weather" }`, drives
  JARVIS through the same intent pipeline voice commands use (weather,
  news, joke, stats, time/date, "jarvis <question>", general chat). Hardware
  actions that need a hand gesture (lock PC, screenshot, launch an
  app) aren't reachable this way on purpose.
- `WS   /ws`      — real-time events as JSON: `{"type": "heard", ...}`,
  `{"type": "reply", "query": ..., "intent": ..., "reply": ...}`

If the port is already in use, the API server just disables itself —
gestures and voice commands keep working without it.

## Reliability

- **Offline fallback.** Builtins (time, date, stats, identity, jokes)
  never touch the network, so they keep working with zero internet.
  Only general chit-chat needs Groq — if that call can't reach the
  internet, times out, or the API key isn't set, JARVIS says so out
  loud instead of erroring silently, and points out it can still do
  time/date. See `llm.get_reply_stream` in the modular backend
  (`llm.py`) for the exact fallback messages.
- **Full conversation transcript.** Every exchange (voice or typed) is
  appended as one JSON line to `data/transcript.log` — the actual
  query/reply text, not just the counts `data/stats.json` tracks.
  Readable with any text editor, `tail -f`, or via the new
  `GET /history` endpoint above. Handled by `translog.py`.
- **Auto-restart on crash.** `start_jarvis.bat` now launches
  `run_jarvis_loop.bat` instead of `main.py` directly — if the backend
  ever exits unexpectedly (crash, unhandled exception), it's
  automatically restarted a few seconds later, and every
  crash/restart is timestamped in `logs\crash.log`. No extra install
  needed; this is plain batch and works out of the box.

  For something more robust than "keep a window open" — auto-start at
  Windows boot, restart even if you're not logged in, run as a proper
  background service — two options:

  **A. Task Scheduler** (built into Windows, no install):
  1. Task Scheduler → Create Task → General tab: "Run whether user is
     logged on or not".
  2. Triggers → New → "At startup" (or "At log on").
  3. Actions → New → Program: `py.exe`, Arguments:
     `-3.11 "C:\path\to\jarvis-project\main.py"`, Start in:
     `C:\path\to\jarvis-project`.
  4. Settings tab → check "If the task fails, restart every" → set to
     1 minute, and raise "Attempt to restart up to" as high as it'll
     let you.

  **B. NSSM** ([nssm.cc](https://nssm.cc), installs JARVIS as a real
  Windows service with automatic restart built in):
  ```
  nssm install JARVIS "C:\path\to\python.exe" "C:\path\to\jarvis-project\main.py"
  nssm set JARVIS AppDirectory "C:\path\to\jarvis-project"
  nssm set JARVIS AppExit Default Restart
  nssm start JARVIS
  ```
  Manage it afterward with `nssm stop JARVIS` / `nssm restart JARVIS`
  / `nssm remove JARVIS confirm`, or via `services.msc` like any other
  Windows service.

## Notes / what changed in this pass

- **Fixed a startup crash**: the script imported a module called
  `assistant_core` that didn't exist anywhere in the project, so it
  would fail immediately with `ModuleNotFoundError`. That import has
  been removed and replaced with real, working code (the stats
  tracker + API server below), rather than left as a dead reference.
- **Removed hardcoded API keys** from the source. They now load from
  `.env` via `python-dotenv`, matching the pattern already used in
  the smaller `config.py`/`data_sources.py` files. See the callout in
  step 2 above about rotating any key that was ever hardcoded.
- **Merged in the modular backend's features**: conversation stats
  (`stats.py`), the FastAPI REST/WebSocket server (`server.py`), and
  the shared intent-routing logic (`llm.py`'s `classify_intent`
  pattern) now live inside `JERVIS.py` itself, so voice commands and
  the new `/command` endpoint share one code path instead of two.
- Added a `stats`/"how many conversations" voice command, which
  previously existed only in the separate `llm.py`, not in `JERVIS.py`.
- Removed a chunk of dead code: a duplicate `"jarvis <question>"`
  handler that could never fire anymore once general commands were
  routed through the shared handler.

## Files

```
JERVIS.py       Main script — gestures, voice, HUD, weather/news/LLM,
                face recognition, stats, and the API server
enroll_face.py  One-time webcam capture to build face_data/<name>/
config.py / data_sources.py / llm.py / main.py / server.py / stats.py /
conversation.py / dispatch.py / master.py / personality.py /
translog.py / tts.py / wake_word.py
                Smaller standalone modules — a lighter, mic-driven
                text-only assistant without the gesture/camera/HUD
                parts. Powers the HUD frontend in frontend/ over the
                local API. Useful as a reference or for a headless
                setup; JERVIS.py does not import them.
run_jarvis_loop.bat / start_jarvis.bat
                One-click launcher with auto-restart on crash — see
                Reliability above.
```
