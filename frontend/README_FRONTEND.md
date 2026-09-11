# JARVIS HUD frontend

A single-page HUD (particle "brain" network, floating HUD panels, live
caption ticker, mic status) that connects to the existing `server.py`
FastAPI backend — no changes needed on the Python side, since
`server.py` already has `CORSMiddleware(allow_origins=["*"])` and a
`/ws` WebSocket + `/stats` + `/command` REST routes.

## Run it

1. Start the Python backend as usual:
   ```
   py -3.11 main.py
   ```
2. Open `frontend/index.html` directly in a browser (double-click it,
   or right-click → Open with browser). No build step, no npm install —
   it's plain HTML/CSS/JS; D3 and the WebGL/three.js-based
   `3d-force-graph` library both load from a CDN, so an internet
   connection is needed on first load (browser caches it after).
3. On first load it auto-tries `localhost:8000`. If your backend runs
   on a different host/port, the boot screen lets you type it in
   (e.g. `192.168.1.20:8000`) and hit **CONNECT**.

## What maps to what

| UI element | Backend source |
|---|---|
| LINK pill (top right) | WebSocket connection state (`/ws`) |
| MIC pill | `wake` / `reply` events over `/ws` |
| Caption ticker (bottom) | `partial`, `heard`, `reply` events |
| Exchange log (bottom-left panel, last 6) | same events, kept as a short scroll |
| HISTORY (top right) | every exchange this session, in a scrollable modal — not just the last 6 |
| 3D particle network (drag to rotate, scroll to zoom, right-drag to pan) | one new node per `reply` event, colored by `intent`, linked to the core |
| Click a node in the graph | opens that exchange's query/reply in a modal |
| THEME (top right) | cycles CYAN → AMBER → LIGHT, saved in the browser (`localStorage`) |
| ☰ panels toggle (mobile only, <900px) | shows/hides the floating HUD panels as a drawer |
| OVERVIEW panel (bottom-right) | polls `GET /stats` every 4s |
| Bottom quick-command buttons + text box | `POST /command` |

## Notes

- Talking to it by voice ("jarvis, what's the weather") drives the UI
  the same way the on-screen buttons and text box do — they all go
  through the same `/command` → `llm.get_reply()` → WebSocket
  broadcast path, so voice and typed input stay in sync automatically.
- If you'd rather not double-click a local HTML file, serve the
  folder with any static server, e.g. `python -m http.server 5500`
  from inside `frontend/`, then open `http://localhost:5500`.
- Want it as one desktop window instead of a browser tab? Wrap it
  with `pywebview`:
  ```python
  import webview
  webview.create_window("JARVIS", "frontend/index.html")
  webview.start()
  ```
  (`pip install pywebview`, run this instead of opening the HTML by
  hand — keep `main.py` running in parallel for the backend.)
- Node/intent colors and the wake word label live at the top of
  `app.js` / in `config.py` respectively if you want to retheme or
  rename things.
