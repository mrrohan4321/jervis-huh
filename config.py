"""
Central config. Everything sensitive is read from the environment /
.env file — NEVER hardcode API keys here. Copy .env.example to .env
and fill in your own values.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ---------- Groq (LLM for chit-chat / general Q&A) ----------
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

# ---------- Weather (OpenWeatherMap) ----------
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "")
WEATHER_CITY = os.getenv("WEATHER_CITY", "Kolkata")

# ---------- Vosk (offline wake word + speech-to-text) ----------
# Download a model from https://alphacephei.com/vosk/models and point
# this at the extracted folder, e.g. models/vosk-model-small-en-us-0.15
VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", "models/vosk-model-small-en-us-0.15")

# One or more wake words/names, comma-separated -- e.g.
#   WAKE_WORDS=jarvis,mira,ab electricals
# so you're not stuck saying "jarvis" specifically. WAKE_WORD (singular)
# still works as a fallback for anyone with an older .env.
_raw_wake_words = os.getenv("WAKE_WORDS", os.getenv("WAKE_WORD", "jarvis"))
WAKE_WORDS = [w.strip().lower() for w in _raw_wake_words.split(",") if w.strip()]
WAKE_WORD = WAKE_WORDS[0]  # kept for any code/log messages wanting a single display name

SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))

# ---------- Server ----------
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))
