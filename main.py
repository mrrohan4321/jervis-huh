"""
Entry point. Wires the pieces together:

  wake_word.VoiceListener --(callbacks)--> dispatch.handle_command
                             --> llm.get_reply_stream --> tts.speak (per sentence)
                             --> server.push_event (WebSocket, per chunk + final)
                             --> stats.record (once, with the full reply)

Commands run on a background thread (not the mic-reading thread), and
each gets an "epoch" number. Hearing the wake word again bumps the
epoch and interrupts TTS immediately -- the older command's dispatch
loop notices it's no longer current and stops generating/speaking
early. This is what makes barge-in work: you can say "jarvis" again to
cut JARVIS off mid-reply and give a new command right away.

Run with:  python main.py
Then either talk to the mic ("jarvis, what's the time?") or drive it
over HTTP:  curl -X POST localhost:8000/command -H "Content-Type: application/json" -d "{\"text\": \"what's the weather\"}"
"""
import threading

import uvicorn

import dispatch
import tts
from config import SERVER_HOST, SERVER_PORT
from server import app, push_event
from wake_word import VoiceListener

_epoch_lock = threading.Lock()
_current_epoch = 0


def _bump_epoch():
    global _current_epoch
    with _epoch_lock:
        _current_epoch += 1
        return _current_epoch


def on_wake():
    print("Wake word heard.")
    tts.interrupt()  # barge-in: cut off whatever JARVIS was still saying
    push_event({"type": "wake"})
    tts.speak("Yes?")


def on_partial(text):
    push_event({"type": "partial", "text": text})


def on_command(text):
    print("Command:", text)
    epoch = _bump_epoch()
    # run on its own thread so the mic-reading loop in VoiceListener.run()
    # is free to hear a new wake word (and interrupt this one) while a
    # reply is still being generated/spoken
    threading.Thread(target=_run_command, args=(text, epoch), daemon=True).start()


def _run_command(text, epoch):
    is_current = lambda: epoch == _current_epoch
    dispatch.handle_command(text, push_event, speak=True, is_current=is_current)


def run_voice_listener():
    try:
        listener = VoiceListener(on_wake=on_wake, on_partial=on_partial, on_command=on_command)
        listener.run()
    except Exception as e:
        # Don't crash the whole service if the mic/model isn't set up
        # yet -- the server (and the text-only /command endpoint) still
        # works without voice input.
        print("Voice listener disabled:", e)


def main():
    tts.start_tts()
    threading.Thread(target=run_voice_listener, daemon=True).start()
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)


if __name__ == "__main__":
    main()
