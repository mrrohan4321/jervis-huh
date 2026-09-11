"""
Full conversation transcript logging -- separate from stats.py, which
only keeps counts/breakdowns, not the actual text. Every exchange
(voice or typed, same as stats.record()) is appended as one JSON line
to data/transcript.log, so the full history survives a restart and can
be grepped/replayed later without living in memory.

One JSON object per line (not one big JSON array) on purpose: it's
append-only-safe -- a crash mid-write can't corrupt earlier lines --
and greppable/tail-able the way a normal log file is.
"""
import json
import threading
import time
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent / "data" / "transcript.log"
_lock = threading.Lock()


def log_exchange(intent, query, reply):
    """Append one exchange as a JSON line. Never raises -- a logging
    hiccup shouldn't take down a live conversation."""
    entry = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "intent": intent,
        "query": query,
        "reply": reply,
    }
    try:
        LOG_FILE.parent.mkdir(exist_ok=True)
        with _lock, LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print("Transcript log write failed:", e)


def read_all():
    """Returns the full transcript as a list of entries, oldest first.
    Used by anything that wants to browse/replay history (e.g. a
    future GET /history endpoint) -- not called anywhere yet, but
    kept here next to the writer instead of scattered elsewhere."""
    if not LOG_FILE.is_file():
        return []
    entries = []
    with LOG_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries
