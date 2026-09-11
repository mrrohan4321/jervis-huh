"""
Short rolling conversation memory so JARVIS can handle follow-ups like
"give me more details on that" or "why?" without you having to repeat
context. In-memory only (resets on restart) and capped at MAX_TURNS
exchanges so the prompt sent to Groq doesn't grow without bound.

Only the "chat" intent actually uses this (see llm.get_reply_stream) --
builtins like time/date/weather don't need conversation context, but
they're still recorded here so a follow-up chat question can refer
back to them (e.g. "why is it that hot").
"""
import threading

MAX_TURNS = 6  # remembers the last 6 exchanges (~12 messages of context)


class ConversationMemory:
    def __init__(self):
        self._lock = threading.Lock()
        self._turns = []  # list of {"query": ..., "reply": ...}

    def add(self, query, reply):
        with self._lock:
            self._turns.append({"query": query, "reply": reply})
            if len(self._turns) > MAX_TURNS:
                self._turns.pop(0)

    def as_messages(self):
        """History formatted as alternating user/assistant messages,
        ready to prepend to a Groq chat completion request."""
        with self._lock:
            msgs = []
            for turn in self._turns:
                msgs.append({"role": "user", "content": turn["query"]})
                msgs.append({"role": "assistant", "content": turn["reply"]})
            return msgs

    def clear(self):
        with self._lock:
            self._turns.clear()


memory = ConversationMemory()
