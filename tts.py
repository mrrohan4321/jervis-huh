"""
Text-to-speech, isolated in its own OS process (not just a thread) —
same pattern used in JERVIS.py, kept here because it's the reliable
fix for pyttsx3/SAPI5's "only the first say()+runAndWait() reliably
produces audio" bug: give every utterance a brand-new engine, in a
process that never imports heavy CV/audio-capture libraries.
"""
import multiprocessing
import time

_tts_queue = None
_tts_process = None


def _tts_worker(q):
    import pyttsx3
    time.sleep(0.3)  # let SAPI5 finish initializing voices
    while True:
        text = q.get()
        if text is None:
            break
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", 175)
            engine.setProperty("volume", 1.0)
            engine.say(text)
            engine.runAndWait()
            del engine
        except Exception as e:
            print("TTS failed:", e)


def start_tts():
    global _tts_queue, _tts_process
    _tts_queue = multiprocessing.Queue(maxsize=8)
    _tts_process = multiprocessing.Process(target=_tts_worker, args=(_tts_queue,), daemon=True)
    _tts_process.start()


def speak(text):
    """Non-blocking — drops the message instead of stalling the caller
    if speech is backed up or TTS hasn't started yet."""
    if _tts_queue is None:
        return
    try:
        _tts_queue.put_nowait(text)
    except Exception:
        pass


def interrupt():
    """Barge-in: immediately silence whatever is currently playing and
    drop anything queued behind it. Used when the wake word is heard
    again while JARVIS is still mid-reply, so a new command doesn't
    have to wait for the old answer to finish talking.

    pyttsx3/SAPI5 doesn't give a clean way to stop audio that's
    already playing from another process, so the reliable fix is the
    blunt one: kill the whole TTS worker process (which kills its
    audio immediately) and start a fresh one right away."""
    global _tts_process
    if _tts_process is not None and _tts_process.is_alive():
        _tts_process.terminate()
        _tts_process.join(timeout=1)
    start_tts()


def stop_tts():
    if _tts_queue is not None:
        _tts_queue.put(None)
    if _tts_process is not None:
        _tts_process.join(timeout=3)

