"""
FastAPI server. Exposes:

  GET  /health    -> liveness check
  GET  /stats     -> conversation counts / uptime / intent breakdown
  GET  /history   -> full transcript (query+reply text) from
                      data/transcript.log -- survives a restart,
                      unlike the in-memory conversation window
  POST /command   -> drive JARVIS with text (no mic needed) -- goes
                      through the exact same dispatch.handle_command()
                      pipeline the voice path uses, so typed commands
                      from the frontend get streamed replies, spoken
                      TTS, and WebSocket events too, not a separate
                      simplified path
  WS   /ws        -> real-time event stream for a frontend: wake
                      events, partial transcripts, streaming reply
                      chunks, final replies, stats pushes, and one
                      "master" event at boot once face recognition
                      finishes (see face_id.py)

push_event() is the bridge between the background voice-listener
thread (and the executor thread /command runs on) and this
asyncio-based server -- it's the only thread-safe way to get a
message from another thread onto the WebSocket broadcast.
"""
import asyncio
import threading

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import dispatch
import face_id
import master
import personality
import tts
from stats import stats
import translog

app = FastAPI(title="JARVIS Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CommandIn(BaseModel):
    text: str


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, payload: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()
_loop = None  # the running asyncio loop, captured on startup


def push_event(payload: dict):
    """Call from ANY thread (e.g. the background voice listener, or
    the executor thread /command runs commands on) to broadcast an
    event to every connected WebSocket client."""
    if _loop is None:
        print("push_event dropped (server not started yet):", payload)
        return
    asyncio.run_coroutine_threadsafe(manager.broadcast(payload), _loop)


@app.on_event("startup")
async def startup():
    global _loop
    _loop = asyncio.get_running_loop()
    # runs off the event loop -- webcam scan takes a few seconds and
    # must never delay /health, /stats, etc. from coming up
    threading.Thread(target=_identify_master_once, daemon=True).start()


def _identify_master_once():
    """Runs once at boot: tries to recognize who's in front of the
    webcam using the samples enroll_face.py collected, greets them by
    name (with a time-of-day greeting) if found, and tells the
    frontend either way (so the HUD can show "MASTER · ROHAN" or
    "MASTER · UNKNOWN")."""
    name = face_id.identify_master()
    master.current_name = name
    if name:
        tts.speak(f"{personality.greeting_for_hour()}, {name}.")
    push_event({"type": "master", "name": name})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/stats")
async def get_stats():
    return stats.snapshot()


@app.get("/history")
async def get_history(limit: int = 200):
    """Full transcript (query+reply text, not just counts) from
    data/transcript.log, most recent last. `limit` caps how many
    entries come back (default 200) so a very long-running JARVIS
    doesn't have to ship its entire history on every request."""
    entries = translog.read_all()
    if limit > 0:
        entries = entries[-limit:]
    return {"count": len(entries), "entries": entries}


@app.post("/command")
async def post_command(cmd: CommandIn):
    # dispatch.handle_command is blocking (streaming HTTP call to Groq
    # + TTS), so it runs in the default executor instead of the async
    # event loop -- this is what keeps /health, /stats, and the /ws
    # broadcast responsive to other clients while one command is
    # still being answered.
    loop = asyncio.get_running_loop()
    intent, reply = await loop.run_in_executor(
        None, dispatch.handle_command, cmd.text, push_event, True
    )
    return {"intent": intent, "reply": reply}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # keep-alive; client pings are ignored
    except WebSocketDisconnect:
        manager.disconnect(ws)
