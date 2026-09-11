"""
"Real data" fetchers -- anything the LLM can't know on its own
(current weather, news headlines, etc.) lives here, kept separate
from the general knowledge / chit-chat handled by llm.py. Add more
functions here (stocks, calendar...) and wire them into
llm.classify_intent / handle_builtin as new intents.
"""
import time
import xml.etree.ElementTree as ET

import requests
from config import WEATHER_API_KEY, WEATHER_CITY

# ---------- simple in-memory weather cache ----------
# Weather doesn't meaningfully change minute to minute, so repeated
# "what's the weather" questions within this window reuse the last
# fetched sentence instead of hitting the API again. Keyed by city
# (lowercased) so different cities cache independently.
WEATHER_CACHE_SECONDS = 600  # 10 minutes
_weather_cache = {}  # city_lower -> (fetched_at, sentence)


def get_weather(city=None):
    """Returns a spoken-friendly current weather sentence, or None if
    unconfigured / the request fails. Never raises."""
    if not WEATHER_API_KEY:
        return "Weather isn't configured yet — set WEATHER_API_KEY in .env."

    city = city or WEATHER_CITY
    key = city.strip().lower()

    cached = _weather_cache.get(key)
    if cached and (time.time() - cached[0]) < WEATHER_CACHE_SECONDS:
        return cached[1]

    try:
        r = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": city, "appid": WEATHER_API_KEY, "units": "metric"},
            timeout=5,
        ).json()
        if str(r.get("cod")) != "200":
            print("Weather request failed:", r.get("message"))
            return cached[1] if cached else None  # serve stale cache over nothing
        temp = round(r["main"]["temp"])
        feels_like = round(r["main"]["feels_like"])
        desc = r["weather"][0]["description"]
        sentence = f"It's currently {temp} degrees in {city}, feels like {feels_like}, with {desc}."
        _weather_cache[key] = (time.time(), sentence)
        return sentence
    except Exception as e:
        print("Weather fetch failed:", e)
        return cached[1] if cached else None  # serve stale cache over nothing


# ---------- news (BBC RSS -- no API key needed) ----------
# Headlines don't meaningfully change minute to minute either, so
# cache the same way weather does.
NEWS_CACHE_SECONDS = 900  # 15 minutes
_news_cache = {"fetched_at": 0.0, "sentence": None}


def get_news_briefing(max_headlines=3):
    """Returns a spoken-friendly top-headlines sentence, or None if
    the feed can't be reached and there's no stale cache to fall back
    on. Never raises."""
    now = time.time()
    if _news_cache["sentence"] and (now - _news_cache["fetched_at"]) < NEWS_CACHE_SECONDS:
        return _news_cache["sentence"]

    try:
        resp = requests.get("http://feeds.bbci.co.uk/news/rss.xml", timeout=6)
        root = ET.fromstring(resp.content)
        titles = [item.findtext("title") for item in root.findall(".//item")]
        titles = [t for t in titles if t][:max_headlines]
        if not titles:
            return _news_cache["sentence"]  # serve stale cache over nothing
        numbered = ". ".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
        sentence = f"Here are today's top headlines. {numbered}."
        _news_cache["fetched_at"] = now
        _news_cache["sentence"] = sentence
        return sentence
    except Exception as e:
        print("News fetch failed:", e)
        return _news_cache["sentence"]  # serve stale cache over nothing, else None
