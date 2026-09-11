"""
Shared command pipeline. Both main.py's on_command (voice) and
server.py's POST /command (typed, from the frontend) call
handle_command() so there's exactly one place where "text in -> LLM ->
speech + UI events + stats" happens -- they can't drift apart.

Why streaming matters here specifically: Groq's reply can take a
second or two to fully generate. Waiting for the whole thing before
speaking or updating the UI makes JARVIS feel laggy. Instead:
  1. chunks arrive from llm.get_reply_stream() as Groq generates them
  2. each chunk is pushed to the frontend immediately (reply_chunk) so
     the caption ticker fills in live, the same way the video's ticker did
  3. as soon as a full sentence is buffered, it's handed to TTS right
     away -- JARVIS starts talking before the rest of the answer has
     even finished generating
  4. once the stream ends, stats are recorded ONCE with the full reply,
     and a final "reply" event carries the complete text (this is what
     the frontend uses to add a node to the particle graph)

Two more pieces wired in here:
  - conversation.memory: the last few exchanges are read before the
    call (as `history`) and the new exchange is recorded after, so
    follow-up questions ("give me more details") have context.
  - `is_current`: voice barge-in support. main.py passes a callback
    that goes False the moment a newer wake word/command comes in, so
    an in-progress reply stops generating/speaking instead of running
    to completion while talking over the new one.

Every completed exchange is also appended to data/transcript.log via
translog.log_exchange() -- the full text, not just counts (that's what
stats.py tracks). Runs right after stats.record() so a stats-only
reader and a transcript-only reader both see a consistent picture.
"""
import re

import llm
import tts
import conversation
import translog
from stats import stats

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def handle_command(text, push_event, speak=True, is_current=None):
    """Runs one command through the full pipeline.
    push_event(dict) is called for every WebSocket event (must be
    safe to call from any thread -- server.push_event already is).
    `is_current`, if given, is checked between sentences/chunks -- if
    it returns False the reply stops generating/speaking early (used
    for voice barge-in: a newer wake word/command supersedes this one).
    Returns (intent, full_reply).
    """
    is_current = is_current or (lambda: True)
    push_event({"type": "heard", "text": text})

    buffer = ""
    full_reply = ""
    intent_out = "chat"
    history = conversation.memory.as_messages()

    for intent, chunk in llm.get_reply_stream(text, stats_snapshot_fn=stats.snapshot, history=history):
        if not is_current():
            break  # superseded by a newer command -- stop generating/speaking
        intent_out = intent
        buffer += chunk
        full_reply += chunk
        push_event({"type": "reply_chunk", "text": full_reply, "intent": intent})

        # speak completed sentences immediately, don't wait for the
        # whole reply to finish generating
        parts = _SENTENCE_END.split(buffer)
        if len(parts) > 1:
            *complete, buffer = parts
            if speak:
                for sentence in complete:
                    if not is_current():
                        break
                    sentence = sentence.strip()
                    if sentence:
                        tts.speak(sentence)

    if is_current() and speak and buffer.strip():
        tts.speak(buffer.strip())

    stats.record(intent_out, text, full_reply)
    if full_reply.strip():
        conversation.memory.add(text, full_reply)
    translog.log_exchange(intent_out, text, full_reply)
    snapshot = stats.snapshot()

    push_event({"type": "reply", "query": text, "intent": intent_out, "reply": full_reply})
    push_event({"type": "stats", **snapshot})

    return intent_out, full_reply
