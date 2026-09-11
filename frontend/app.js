/* =========================================================
   JARVIS HUD frontend
   Talks to the FastAPI backend in server.py:
     GET  /health   liveness
     GET  /stats    conversation stats (polled as a slow fallback)
     POST /command  send a typed command
     WS   /ws       live events: wake / partial / heard / reply_chunk /
                     reply / stats / master
   ========================================================= */

const INTENT_COLOR = {
  weather:  '#4dd9ff',
  stats:    '#ffcc4d',
  time:     '#5cff9d',
  date:     '#5cff9d',
  identity: '#ff6bb0',
  news:     '#ff9f4d',
  joke:     '#ffe14d',
  chat:     '#b76bff',
  default:  '#7fa8b8',
};

let API_BASE = 'http://localhost:8000';
let WS_URL   = 'ws://localhost:8000/ws';
let ws = null;
let wsRetryTimer = null;

/* ---------------- boot / connect ---------------- */
const bootOverlay = document.getElementById('bootOverlay');
const bootStatus  = document.getElementById('bootStatus');
const hostInput   = document.getElementById('hostInput');

document.getElementById('bootConnect').addEventListener('click', tryConnect);
hostInput.addEventListener('keydown', e => { if (e.key === 'Enter') tryConnect(); });

function tryConnect(){
  const host = hostInput.value.trim() || 'localhost:8000';
  API_BASE = `http://${host}`;
  WS_URL   = `ws://${host}/ws`;
  bootStatus.textContent = 'contacting backend…';

  fetch(`${API_BASE}/health`).then(r => {
    if (!r.ok) throw new Error('bad status');
    bootOverlay.classList.add('hidden');
    connectWS();
    pollStats();
    setInterval(pollStats, STATS_POLL_MS);
  }).catch(() => {
    bootStatus.textContent = `couldn't reach ${API_BASE} — is main.py running?`;
  });
}

// auto-try the default host once on load, silently
window.addEventListener('load', () => {
  fetch(`${API_BASE}/health`).then(r => {
    if (r.ok){
      bootOverlay.classList.add('hidden');
      connectWS();
      pollStats();
      setInterval(pollStats, STATS_POLL_MS);
    }
  }).catch(() => { /* stay on boot screen, user can edit host */ });
});

/* ---------------- websocket ---------------- */
const pillConn = document.getElementById('pillConn');
const pillMic  = document.getElementById('pillMic');
const linkState = document.getElementById('linkState');

function connectWS(){
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    setPill(pillConn, true, 'LINK · LIVE');
    linkState.textContent = 'CONNECTED';
    clearTimeout(wsRetryTimer);
  };

  ws.onclose = () => {
    setPill(pillConn, false, 'LINK · OFFLINE');
    linkState.textContent = 'RECONNECTING';
    wsRetryTimer = setTimeout(connectWS, 2500);
  };

  ws.onerror = () => ws.close();

  ws.onmessage = (msg) => {
    let payload;
    try { payload = JSON.parse(msg.data); } catch { return; }
    handleEvent(payload);
  };
}

function setPill(el, on, label){
  el.textContent = '';
  const dot = document.createElement('span');
  dot.className = 'dot';
  el.appendChild(dot);
  el.appendChild(document.createTextNode(label));
  el.classList.toggle('on', on);
}

/* ---------------- events -> UI ---------------- */
const ticker = document.getElementById('tickerText');
const logBody = document.getElementById('logBody');

function handleEvent(evt){
  if (evt.type === 'wake'){
    setPill(pillMic, true, 'MIC · AWAKE');
    ticker.textContent = 'yes?';
    ticker.className = 'ticker-text listening';
    pulseCore();
  }
  else if (evt.type === 'partial'){
    ticker.textContent = evt.text || '…';
    ticker.className = 'ticker-text listening';
  }
  else if (evt.type === 'heard'){
    ticker.textContent = evt.text;
    ticker.className = 'ticker-text thinking';
    addLog('you', evt.text);
  }
  else if (evt.type === 'reply_chunk'){
    // streamed straight from Groq as it generates -- the ticker fills
    // in live instead of waiting for the full reply
    ticker.textContent = evt.text + ' ▌';
    ticker.className = 'ticker-text thinking';
    bumpWave();
  }
  else if (evt.type === 'reply'){
    setPill(pillMic, false, 'MIC · IDLE');
    ticker.textContent = evt.reply;
    ticker.className = 'ticker-text';
    addLog('jarvis', evt.reply, evt.intent);
    addNode(evt.intent, evt.query, evt.reply);
    bumpWave();
  }
  else if (evt.type === 'stats'){
    // pushed after every exchange -- keeps the OVERVIEW panel current
    // without waiting for the next poll tick
    applyStats(evt);
  }
  else if (evt.type === 'master'){
    // pushed once at boot after the webcam face-scan finishes
    const pillMaster = document.getElementById('pillMaster');
    if (evt.name){
      setPill(pillMaster, true, `MASTER · ${evt.name.toUpperCase()}`);
    } else {
      setPill(pillMaster, false, 'MASTER · UNKNOWN');
    }
  }
}

/* full, untrimmed record of every exchange — the small EXCHANGE LOG
   panel only ever shows the last 6, this backs the HISTORY modal */
let fullLog = [];

function addLog(who, text, intent){
  fullLog.push({ who, text, intent, time: new Date() });
  renderHistory();

  const row = document.createElement('div');
  row.className = 'row' + (who === 'jarvis' ? ' reply' : '');
  const whoSpan = document.createElement('span');
  whoSpan.className = 'who';
  whoSpan.textContent = who === 'jarvis' ? 'JVS>' : 'YOU>';
  const txt = document.createElement('span');
  txt.textContent = text.length > 60 ? text.slice(0, 60) + '…' : text;
  row.appendChild(whoSpan);
  row.appendChild(txt);
  logBody.prepend(row);
  while (logBody.children.length > 6) logBody.removeChild(logBody.lastChild);
}

/* ---------------- history modal ---------------- */
const historyModal = document.getElementById('historyModal');
const historyBody  = document.getElementById('historyBody');
const historyCount = document.getElementById('historyCount');

function fmtTime(d){
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function renderHistory(){
  historyCount.textContent = fullLog.length ? `(${fullLog.length})` : '';
  if (!historyModal.classList.contains('open')) return; // only paint while visible
  paintHistory();
}

function paintHistory(){
  historyBody.innerHTML = '';
  if (!fullLog.length){
    historyBody.innerHTML = '<div class="history-empty">no exchanges yet</div>';
    return;
  }
  // newest first
  for (let i = fullLog.length - 1; i >= 0; i--){
    const e = fullLog[i];
    const row = document.createElement('div');
    row.className = 'history-row' + (e.who === 'jarvis' ? ' reply' : '');
    row.innerHTML = `
      <span class="history-time">${fmtTime(e.time)}</span>
      <span class="history-who">${e.who === 'jarvis' ? 'JVS' : 'YOU'}</span>
      <span class="history-text"></span>`;
    row.querySelector('.history-text').textContent = e.text; // textContent: avoid HTML injection
    historyBody.appendChild(row);
  }
}

function openModal(el){ el.classList.add('open'); }
function closeModal(el){ el.classList.remove('open'); }

document.getElementById('historyBtn').addEventListener('click', () => {
  paintHistory();
  openModal(historyModal);
});
document.getElementById('historyModalClose').addEventListener('click', () => closeModal(historyModal));
historyModal.addEventListener('click', e => { if (e.target === historyModal) closeModal(historyModal); });

/* ---------------- node detail modal ---------------- */
const nodeModal = document.getElementById('nodeModal');
const nodeModalTitle = document.getElementById('nodeModalTitle');
const nodeModalBody  = document.getElementById('nodeModalBody');

function openNodeModal(node){
  nodeModalTitle.textContent = (node.intent || 'chat').toUpperCase() + (node.time ? ' · ' + fmtTime(node.time) : '');
  nodeModalBody.innerHTML = `
    <div class="detail-row">
      <div class="detail-label">YOU ASKED</div>
      <div class="detail-text" id="nodeModalQuery"></div>
    </div>
    <div class="detail-row">
      <div class="detail-label">JARVIS REPLIED</div>
      <div class="detail-text" id="nodeModalReply"></div>
    </div>`;
  nodeModalBody.querySelector('#nodeModalQuery').textContent = node.query || '(no query text)';
  nodeModalBody.querySelector('#nodeModalReply').textContent = node.reply || '(no reply text)';
  openModal(nodeModal);
}
document.getElementById('nodeModalClose').addEventListener('click', () => closeModal(nodeModal));
nodeModal.addEventListener('click', e => { if (e.target === nodeModal) closeModal(nodeModal); });

document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  closeModal(nodeModal);
  closeModal(historyModal);
});

/* ---------------- theme switch ---------------- */
const THEMES = ['cyan', 'amber', 'light'];
const themeBtn = document.getElementById('themeBtn');
let themeIdx = 0;
try {
  const saved = localStorage.getItem('jarvis-theme');
  const i = THEMES.indexOf(saved);
  if (i !== -1) themeIdx = i;
} catch { /* localStorage unavailable, fall back to default */ }

function applyTheme(){
  const t = THEMES[themeIdx];
  if (t === 'cyan') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', t);
  themeBtn.textContent = t.toUpperCase();
  try { localStorage.setItem('jarvis-theme', t); } catch { /* ignore */ }
}
themeBtn.addEventListener('click', () => {
  themeIdx = (themeIdx + 1) % THEMES.length;
  applyTheme();
});
applyTheme();

/* ---------------- mobile HUD-panels drawer ---------------- */
const hudPanels = document.getElementById('hudPanels');
document.getElementById('panelsBtn').addEventListener('click', () => {
  hudPanels.classList.toggle('show');
});

/* ---------------- stats polling ---------------- */
const statConv = document.getElementById('statConv');
const statUptime = document.getElementById('statUptime');
const intentList = document.getElementById('intentList');
const barUptime = document.getElementById('barUptime');
const barActivity = document.getElementById('barActivity');

let lastConvCount = 0;

// Stats now arrive two ways: pushed over the WebSocket right after
// every exchange (instant), and polled here as a slow-interval
// fallback/resync in case a push was missed (e.g. briefly disconnected).
// Poll interval is deliberately slow since the push covers the common case.
const STATS_POLL_MS = 15000;

function applyStats(s){
  statConv.textContent = s.conversations_answered;
  statUptime.textContent = fmtUptime(s.uptime_seconds);
  barUptime.style.width = Math.min(100, (s.uptime_seconds / 3600) * 100) + '%';

  const activityPulse = s.conversations_answered > lastConvCount ? 100 : 20;
  barActivity.style.width = activityPulse + '%';
  lastConvCount = s.conversations_answered;

  intentList.innerHTML = '';
  Object.entries(s.intent_breakdown || {}).forEach(([k, v]) => {
    const chip = document.createElement('div');
    chip.className = 'intent-chip';
    chip.innerHTML = `<span style="color:${INTENT_COLOR[k] || INTENT_COLOR.default}">${k.toUpperCase()}</span><span class="n">${v}</span>`;
    intentList.appendChild(chip);
  });
}

function pollStats(){
  fetch(`${API_BASE}/stats`).then(r => r.json()).then(applyStats)
    .catch(() => { /* backend not up yet, ignore */ });
}

function fmtUptime(sec){
  const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = sec%60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

/* ---------------- quick commands / text input ---------------- */
document.getElementById('botnav').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-cmd]');
  if (!btn) return;
  sendCommand(btn.dataset.cmd);
});

document.getElementById('askForm').addEventListener('submit', (e) => {
  e.preventDefault();
  const input = document.getElementById('askInput');
  const text = input.value.trim();
  if (!text) return;
  sendCommand(text);
  input.value = '';
});

function sendCommand(text){
  addLog('you', text);
  ticker.textContent = text;
  ticker.className = 'ticker-text thinking';
  fetch(`${API_BASE}/command`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text}),
  }).catch(() => {
    ticker.textContent = "can't reach the backend right now.";
  });
  // the /reply WS event (broadcast by the server) updates the ticker,
  // log and graph — no need to handle the POST response directly.
}

/* ---------------- waveform ---------------- */
const waveSvg = d3.select('#wave');
let wavePath = waveSvg.append('path');
let waveTick = 0;
let waveBump = 0;

function bumpWave(){ waveBump = 18; }

function drawWave(){
  waveTick += 0.15;
  waveBump = Math.max(0, waveBump - 0.6);
  const pts = [];
  const amp = 4 + waveBump;
  for (let x = 0; x <= 220; x += 4){
    const y = 30 + Math.sin(x/10 + waveTick) * amp * Math.sin(waveTick*0.3 + x*0.02);
    pts.push([x, y]);
  }
  const line = d3.line().curve(d3.curveBasis);
  wavePath.attr('d', line(pts));
  requestAnimationFrame(drawWave);
}
requestAnimationFrame(drawWave);

/* ---------------- scope idle rotation (pure CSS handles the sweep) ---------------- */

/* =========================================================
   3D force-directed knowledge graph — the "brain" visualization.
   A bright core node sits at the center. Each answered exchange
   spawns a small node linked to the core, colored by intent, and
   old nodes fall off once the graph gets crowded.
   Rendered with three.js via the 3d-force-graph library — drag to
   rotate, scroll/middle-drag to zoom, right-drag to pan.
   ========================================================= */
const CORE_ID = '__core__';
let nodes = [{ id: CORE_ID, core: true, r: 14, color: '#eafcff' }];
let links = [];
let nodeSeq = 0;
const MAX_NODES = 46;

function isCoreRef(x){ return x === CORE_ID || (x && x.core); }

const Graph = ForceGraph3D()(document.getElementById('graph3d'))
  .backgroundColor('rgba(0,0,0,0)')     // let the CSS space-gradient show through
  .showNavInfo(false)                   // the botnav already carries controls hints
  .nodeId('id')
  .nodeLabel(d => d.core ? 'JARVIS' : `${(d.intent||'chat').toUpperCase()}: ${d.query || ''}`)
  .nodeVal(d => d.core ? 40 : (d.r || 6))
  .nodeColor(d => d.color)
  .nodeOpacity(0.95)
  .nodeResolution(16)
  .linkColor(d => d.color)
  .linkOpacity(0.35)
  .linkWidth(d => isCoreRef(d.source) || isCoreRef(d.target) ? 1 : 0.6)
  .linkDirectionalParticles(1)
  .linkDirectionalParticleWidth(1.5)
  .linkDirectionalParticleColor(d => d.color)
  .linkDirectionalParticleSpeed(0.006)
  .onNodeClick(node => { if (node && !node.core) openNodeModal(node); })
  .onNodeHover(node => {
    document.getElementById('graph3d').style.cursor = node && !node.core ? 'pointer' : 'grab';
  })
  .graphData({ nodes, links });

Graph.d3Force('charge').strength(-85);
Graph.d3Force('link').distance(l => isCoreRef(l.source) || isCoreRef(l.target) ? 130 : 46).strength(0.5);
Graph.cameraPosition({ x: 0, y: 0, z: 260 });

// slow idle rotation, like the sweep on the mic scope — user drag overrides it
Graph.controls().autoRotate = true;
Graph.controls().autoRotateSpeed = 0.35;
Graph.controls().enableDamping = true;

function render(){
  Graph.graphData({ nodes, links });
}

function addNode(intent, query, reply){
  const color = INTENT_COLOR[intent] || INTENT_COLOR.default;
  const id = 'n' + (nodeSeq++);
  const node = { id, r: 5 + Math.random()*3, color, intent, query, reply, time: new Date() };
  nodes.push(node);
  links.push({ id: 'l' + id, source: CORE_ID, target: id, color });

  if (nodes.length > MAX_NODES){
    const dead = nodes.splice(1, nodes.length - MAX_NODES);
    const deadIds = new Set(dead.map(d => d.id));
    links = links.filter(l => !deadIds.has(l.source.id || l.source) && !deadIds.has(l.target.id || l.target));
  }
  render();
}

function pulseCore(){
  const el = document.getElementById('corePulse');
  el.classList.remove('go');
  void el.offsetWidth; // restart the CSS animation
  el.classList.add('go');
}

function resizeGraph(){
  Graph.width(window.innerWidth).height(window.innerHeight);
}
window.addEventListener('resize', resizeGraph);
resizeGraph();
