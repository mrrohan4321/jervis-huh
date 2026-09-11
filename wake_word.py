"""
Wake word detection + speech-to-text, both via Vosk (fully offline,
no account/API key needed — unlike Porcupine).

One continuous recognizer runs on the live mic stream. A small state
machine decides what each recognized chunk of text means:

  IDLE       -> waiting to hear any configured wake word in a result
  LISTENING  -> the next thing said is treated as the command

Saying the wake word and the command in one breath ("jarvis what's
the weather") also works — the wake word is stripped off and
whatever's left is treated as the command immediately.

Multiple wake words/names are supported (WAKE_WORDS in config.py, e.g.
"jarvis,mira,ab electricals") — any one of them triggers the same way.

Download a model first: https://alphacephei.com/vosk/models
(vosk-model-small-en-us-0.15 is a good small/fast default), unzip it,
and point VOSK_MODEL_PATH in .env at the folder.
"""
import json
import queue
import threading

import sounddevice as sd
from vosk import Model, KaldiRecognizer

from config import VOSK_MODEL_PATH, WAKE_WORDS, SAMPLE_RATE


def _find_wake_word(text):
    """Returns the first configured wake word found in text, or None.
    Checked in the order listed in WAKE_WORDS."""
    for w in WAKE_WORDS:
        if w in text:
            return w
    return None


class VoiceListener:
    def __init__(self, on_wake=None, on_partial=None, on_command=None):
        try:
            self.model = Model(VOSK_MODEL_PATH)
        except Exception as e:
            raise RuntimeError(
                f"Couldn't load Vosk model at '{VOSK_MODEL_PATH}'. "
                f"Download one from https://alphacephei.com/vosk/models, "
                f"unzip it, and set VOSK_MODEL_PATH in .env. Original error: {e}"
            )
        self.rec = KaldiRecognizer(self.model, SAMPLE_RATE)
        self.audio_q = queue.Queue()
        self.on_wake = on_wake or (lambda: None)
        self.on_partial = on_partial or (lambda t: None)
        self.on_command = on_command or (lambda t: None)
        self._awake = False
        self._stop = threading.Event()

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            print("Audio status:", status)
        self.audio_q.put(bytes(indata))

    def run(self):
        """Blocking — call this in a background thread."""
        with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=8000,
                                dtype="int16", channels=1,
                                callback=self._audio_callback):
            print(f"Voice listener active — say any of {WAKE_WORDS} to wake me.")
            while not self._stop.is_set():
                data = self.audio_q.get()

                if self.rec.AcceptWaveform(data):
                    text = json.loads(self.rec.Result()).get("text", "").strip()
                    if not text:
                        continue
                    self._handle_final(text)
                else:
                    partial = json.loads(self.rec.PartialResult()).get("partial", "")
                    if partial:
                        self.on_partial(partial)
                        # Fire the wake event as soon as it's heard, even
                        # mid-utterance, for snappier UI feedback — the
                        # actual command still comes from the final result.
                        # self._awake is already back to False by the time
                        # a previous command is still generating/speaking
                        # (main.py hands it off to a background thread), so
                        # this same check also catches barge-in: saying the
                        # wake word again while JARVIS is still mid-reply.
                        if not self._awake and _find_wake_word(partial.lower()):
                            self._awake = True
                            self.on_wake()

    def _handle_final(self, text):
        lowered = text.lower()
        hit = _find_wake_word(lowered)
        if not self._awake:
            if hit:
                self._awake = True
                self.on_wake()
                remainder = lowered.split(hit, 1)[-1].strip(" ,.")
                if remainder:
                    self.on_command(remainder)
                    self._awake = False
            # else: not awake and no wake word — ignore, keep listening
        else:
            # strip a leading wake word too, in case it was repeated
            # ("jarvis, jarvis what's the time")
            remainder = lowered.split(hit, 1)[-1].strip(" ,.") if hit else lowered
            self.on_command(remainder)
            self._awake = False

    def stop(self):
        self._stop.set()
