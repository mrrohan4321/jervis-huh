"""
Business logic: tracks how many conversations JARVIS has answered,
broken down by intent, and persists it to disk so counts survive a
restart. Thread-safe -- record() is called from the background voice
thread and from FastAPI request handlers.

Disk writes are batched instead of happening on every single record():
a save is triggered every SAVE_EVERY_N conversations, and a background
thread flushes any pending ("dirty") change at least every
SAVE_EVERY_SECONDS even if that count isn't reached -- so a burst of
chatting doesn't turn into a disk write per exchange, but you never
lose more than ~SAVE_EVERY_SECONDS of history if the process dies.
"""
import json
import threading
import time
from collections import Counter
from pathlib import Path

STATS_FILE = Path(__file__).resolve().parent / "data" / "stats.json"
SAVE_EVERY_N = 5
SAVE_EVERY_SECONDS = 30


class StatsTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.conversations = 0
        self.intent_counts = Counter()
        self.last_intent = None
        self.last_query = None
        self.last_reply = None
        self._dirty = False
        STATS_FILE.parent.mkdir(exist_ok=True)
        self._load()
        threading.Thread(target=self._flush_loop, daemon=True).start()

    def _load(self):
        if STATS_FILE.is_file():
            try:
                data = json.loads(STATS_FILE.read_text())
                self.conversations = data.get("conversations", 0)
                self.intent_counts = Counter(data.get("intent_counts", {}))
            except Exception as e:
                print("Stats load failed, starting fresh:", e)

    def _save_locked(self):
        """Caller must hold self._lock."""
        try:
            STATS_FILE.write_text(json.dumps({
                "conversations": self.conversations,
                "intent_counts": dict(self.intent_counts),
            }, indent=2))
            self._dirty = False
        except Exception as e:
            print("Stats save failed:", e)

    def _flush_loop(self):
        """Background safety net: even if SAVE_EVERY_N conversations
        never happens, don't let more than ~SAVE_EVERY_SECONDS of
        history sit unsaved."""
        while True:
            time.sleep(SAVE_EVERY_SECONDS)
            with self._lock:
                if self._dirty:
                    self._save_locked()

    def record(self, intent, query, reply):
        """Call once per completed exchange (voice or text)."""
        with self._lock:
            self.conversations += 1
            self.intent_counts[intent] += 1
            self.last_intent = intent
            self.last_query = query
            self.last_reply = reply
            self._dirty = True
            if self.conversations % SAVE_EVERY_N == 0:
                self._save_locked()

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

    def flush(self):
        """Force-save immediately -- call on clean shutdown if you add one."""
        with self._lock:
            if self._dirty:
                self._save_locked()


stats = StatsTracker()
