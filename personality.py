"""
Small optional personality flourishes -- an occasional dry one-liner
tacked onto a chat reply, and a dedicated "tell me a joke" intent.
Kept separate from llm.py's core logic so the lines themselves are
easy to trim/rewrite without touching the reply pipeline.
"""
import random

QUIP_CHANCE = 0.15  # roughly 1 in 7 chat replies gets a tiny aside

_QUIPS = (
    "Just saying.",
    "Don't quote me on that one.",
    "I did not make that up, for once.",
    "You're welcome, by the way.",
    "I live for these questions.",
    "Filed under things I know.",
    "I'll allow myself that one.",
)

_JOKES = (
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "I told my laptop I needed a break, and it said fine, I'll sleep too.",
    "There are 10 types of people -- those who understand binary, and those who don't.",
    "I'd tell you a UDP joke, but you might not get it.",
    "Why did the AI go to therapy? Too many unresolved dependencies.",
    "I'm not saying I'm smart, but I've never once lost a game of chess against myself.",
)


def maybe_quip():
    """Small random chance of a short aside to tack onto a chat reply.
    Returns None most of the time -- that's the point."""
    if random.random() < QUIP_CHANCE:
        return random.choice(_QUIPS)
    return None


def random_joke():
    return random.choice(_JOKES)


def greeting_for_hour(hour=None):
    """Time-of-day greeting word ('Good morning' etc). Pass an hour
    (0-23) for testing; defaults to the real current local hour."""
    import time
    if hour is None:
        hour = int(time.strftime("%H"))
    if hour < 5:
        return "Still up"
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    if hour < 21:
        return "Good evening"
    return "Good night"
