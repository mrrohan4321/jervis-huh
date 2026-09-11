"""
AI logic / intent handling.

classify_intent() does cheap keyword matching first -- time, date,
weather, stats, identity all get answered instantly without an LLM
call. Anything else falls through to Groq for a real conversational
reply.

get_reply_stream() is the entry point everything now uses (voice via
main.py, typed commands via server.py, both through dispatch.py).
It's a generator that yields (intent, text_chunk) pairs:
  - builtin/weather intents yield exactly one chunk (the whole reply
    is already known instantly, nothing to stream)
  - "chat" intent streams token deltas straight from Groq's streaming
    API as they arrive, so the caller can start speaking/displaying
    the first sentence before the rest has even been generated
"""
import json
import time

import requests

from config import GROQ_API_KEY, GROQ_MODEL
import data_sources
import conversation
import master
import personality

SYSTEM_PROMPT = (
    "You are JARVIS, Rohan's personal AI assistant. When asked a factual "
    "or how-to question, actually answer it directly with real, correct "
    "information -- give proper 'gyan', don't be vague or deflect. You "
    "may be given the last few exchanges as conversation history -- use "
    "it to resolve follow-ups like 'give me more details' or 'why' "
    "naturally, the way a person remembering the conversation would. "
    "Reply in 1-3 short sentences suitable for text-to-speech -- "
    "no lists, no markdown, no long explanations."
)

_RESET_PHRASES = ("forget everything", "forget what we talked", "clear memory",
                   "clear your memory", "start over", "reset memory")


def classify_intent(text):
    t = text.lower()
    if any(p in t for p in _RESET_PHRASES):
        return "reset_memory"
    if "weather" in t:
        return "weather"
    if "news" in t or "headline" in t:
        return "news"
    if "joke" in t or "make me laugh" in t or "something funny" in t:
        return "joke"
    if "date" in t or "day is it" in t:
        return "date"
    if "time" in t:
        return "time"
    if "stat" in t or ("how many" in t and "convers" in t):
        return "stats"
    if "who are you" in t or "what are you" in t:
        return "identity"
    return "chat"


def handle_builtin(intent, stats_snapshot_fn=None):
    """Returns a reply string for intents that don't need the LLM or
    any external service, or None if this intent needs more work
    (weather / news / chat) that get_reply_stream() handles separately."""
    if intent == "time":
        return f"It's {time.strftime('%I:%M %p')}."
    if intent == "date":
        return f"Today is {time.strftime('%A, %B %d')}."
    if intent == "identity":
        if master.current_name:
            return f"I am JARVIS, {master.current_name}'s personal assistant."
        return "I am JARVIS, your personal assistant."
    if intent == "joke":
        return personality.random_joke()
    if intent == "stats" and stats_snapshot_fn:
        s = stats_snapshot_fn()
        return f"I've answered {s['conversations_answered']} conversations so far."
    if intent == "reset_memory":
        conversation.memory.clear()
        return "Okay, I've cleared what we talked about -- clean slate."
    return None


def ask_groq_stream(question, history=None):
    """Generator yielding text deltas from a streaming Groq chat
    completion. `history` is an optional list of {"role", "content"}
    messages (oldest first) inserted between the system prompt and the
    current question, so follow-up questions have context.

    Raises instead of swallowing the error -- RuntimeError if the key
    isn't set, or whatever `requests` raises (ConnectionError, Timeout,
    HTTPError, ...) on a failed call. get_reply_stream() is what turns
    those into a spoken-friendly fallback line, so it can pick the
    right message for "no internet" vs. "no API key" vs. anything else,
    and skip tacking on a personality quip when the call didn't
    actually succeed."""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY isn't set in .env.")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})

    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_MODEL,
            "messages": messages,
            "max_tokens": 150,
            "temperature": 0.6,
            "stream": True,
        },
        timeout=15,
        stream=True,
    )
    r.raise_for_status()
    for raw_line in r.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8", errors="ignore")
        if not line.startswith("data: "):
            continue
        data = line[len("data: "):].strip()
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        delta = obj.get("choices", [{}])[0].get("delta", {}).get("content")
        if delta:
            yield delta


def get_reply_stream(text, stats_snapshot_fn=None, history=None):
    """Main entry point. Generator: yields (intent, chunk) pairs.
    Concatenate the chunks for a given call to get the full reply.
    `history` (see ask_groq_stream) is only used for the "chat" intent
    -- builtins/weather/news don't need it."""
    intent = classify_intent(text)

    if intent == "weather":
        reply = data_sources.get_weather() or "I couldn't fetch the weather right now."
        yield intent, reply
        return

    if intent == "news":
        reply = data_sources.get_news_briefing() or "I couldn't reach the news right now."
        yield intent, reply
        return

    builtin = handle_builtin(intent, stats_snapshot_fn)
    if builtin is not None:
        yield intent, builtin
        return

    # "chat" falls through to Groq. Offline / misconfigured / erroring
    # all get a friendly canned line instead of a stack trace -- the
    # builtins above (time, date, stats, identity...) still work with
    # zero internet, so it's worth telling the person that.
    try:
        got_any_chunk = False
        for chunk in ask_groq_stream(text, history=history):
            got_any_chunk = True
            yield intent, chunk
        if got_any_chunk:
            quip = personality.maybe_quip()
            if quip:
                yield intent, " " + quip
    except requests.exceptions.ConnectionError:
        print("Groq call failed: no internet connection")
        yield intent, ("Looks like I don't have an internet connection right now, "
                        "but I can still tell you the time or the date if that helps.")
    except requests.exceptions.Timeout:
        print("Groq call failed: timed out")
        yield intent, "My connection to the language model timed out -- mind trying again in a moment?"
    except RuntimeError as e:
        print("Groq call failed:", e)
        yield intent, f"I can't reach my language model -- {e}"
    except Exception as e:
        print("Groq call failed:", e)
        yield intent, "I ran into an error reaching my language model just now."


def get_reply(text, stats_snapshot_fn=None, history=None):
    """Non-streaming convenience wrapper (kept for any external script
    or REPL that just wants a single (intent, full_reply) tuple)."""
    intent = None
    parts = []
    for intent, chunk in get_reply_stream(text, stats_snapshot_fn, history=history):
        parts.append(chunk)
    return intent, "".join(parts)
