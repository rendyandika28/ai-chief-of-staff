"""Read-only monitoring dashboard for the AI agent.

Reads the same SQLite files the bot writes (MEMORY_DIR, mounted read-only in the
Coolify container). Serves one polished page that polls /api/state. Standalone:
imports only stdlib + fastapi + app.lib.events, so the container stays light.

Run:  uv run uvicorn app.interfaces.dashboard:app --host 0.0.0.0 --port 8000
Auth: HTTP Basic via DASH_USER / DASH_PASS env vars.
"""

import os
import secrets
import sqlite3
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.lib.events import recent

MEMORY_DIR = os.getenv("MEMORY_DIR", "memory")

app = FastAPI(title="Chief Ops Console", docs_url=None, redoc_url=None)
_security = HTTPBasic()


def auth(cred: HTTPBasicCredentials = Depends(_security)) -> bool:
    user = os.getenv("DASH_USER", "admin")
    pw = os.getenv("DASH_PASS")
    if not pw:
        raise HTTPException(500, "DASH_PASS belum di-set di environment")
    ok = secrets.compare_digest(cred.username, user) and secrets.compare_digest(cred.password, pw)
    if not ok:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Login salah",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


def _q(dbname: str, sql: str, params=()):
    path = os.path.join(MEMORY_DIR, dbname)
    if not os.path.exists(path):
        return []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()
    except Exception:
        return []


def _scalar(dbname: str, sql: str, params=()) -> int:
    r = _q(dbname, sql, params)
    return r[0][0] if r and r[0] and r[0][0] is not None else 0


def _age(ts: str):
    """Detik sejak ts (UTC "YYYY-MM-DD HH:MM:SS"). Pakai jam server = jam penulis, no skew."""
    if not ts:
        return None
    try:
        s = ts.replace("T", " ").split(".")[0]
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return None


def _status(events: list) -> str:
    """awake | restarting | down — dihitung server-side dari heartbeat & marker deploy."""
    hb_i = next((i for i, e in enumerate(events) if e["kind"] == "heartbeat"), None)
    dep_i = next((i for i, e in enumerate(events) if e["kind"] == "deploy"), None)
    # deploy lebih baru dari heartbeat terakhir → lagi restart
    if dep_i is not None and (hb_i is None or dep_i < hb_i):
        return "restarting"
    age = _age(events[hb_i]["ts"]) if hb_i is not None else None
    if age is None:
        return "down"
    if age < 90:
        return "awake"
    if age < 360:        # heartbeat baru berhenti < 6 mnt → kemungkinan lagi restart
        return "restarting"
    return "down"


def build_state() -> dict:
    events = recent(200)
    feed = [e for e in events if e["kind"] != "heartbeat"][:60]
    last_beat = next((e["ts"] for e in events if e["kind"] == "heartbeat"), None)
    last_error = next((e for e in events if e["kind"] == "error"), None)

    chat = [
        {"role": r[0], "content": r[1], "ts": r[2]}
        for r in reversed(
            _q("conversations.db", "SELECT role, content, timestamp FROM messages ORDER BY id DESC LIMIT 60")
        )
    ]
    memory = [
        {"subject": r[0], "predicate": r[1], "object": r[2], "confidence": r[3], "updated_at": r[4]}
        for r in _q(
            "knowledge.db",
            "SELECT subject, predicate, object, confidence, updated_at FROM facts ORDER BY updated_at DESC LIMIT 100",
        )
    ]
    reminders = [
        {"message": r[0], "run_at": r[1]}
        for r in _q(
            "scheduler.db",
            "SELECT message, run_at FROM tasks WHERE status='pending' ORDER BY run_at LIMIT 50",
        )
    ]

    return {
        "status": _status(events),
        "last_beat": last_beat,
        "last_active": events[0]["ts"] if events else None,
        "last_error": last_error,
        "stats": {
            "messages": _scalar("conversations.db", "SELECT COUNT(*) FROM messages"),
            "tools_24h": _scalar(
                "events.db",
                "SELECT COUNT(*) FROM events WHERE kind='tool' AND ts >= datetime('now','-1 day')",
            ),
            "reminders": len(reminders),
            "facts": len(memory),
        },
        "feed": feed,
        "chat": chat,
        "memory": memory,
        "reminders": reminders,
    }


@app.get("/api/state")
def api_state(_: bool = Depends(auth)):
    return JSONResponse(build_state())


@app.get("/", response_class=HTMLResponse)
def index(_: bool = Depends(auth)):
    return HTMLResponse(PAGE)


PAGE = r"""<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chief · Ops Console</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com"></script>
<script>
tailwind.config = { theme: { extend: {
  colors: {
    ink:'#0B0E14', surface:'#12171F', surface2:'#171D27', line:'#232B37',
    txt:'#E7ECF3', muted:'#7C8798',
    mint:'#6EE7C7', amber:'#F4B740', coral:'#F26D6D', azure:'#83A9F5',
  },
  fontFamily:{ display:['"Space Grotesk"','sans-serif'], body:['Inter','sans-serif'], mono:['"IBM Plex Mono"','monospace'] },
}}}
</script>
<style>
  body{background:#0B0E14;}
  ::-webkit-scrollbar{width:8px;height:8px}
  ::-webkit-scrollbar-thumb{background:#232B37;border-radius:8px}
  .breathe{animation:breathe 2.4s ease-in-out infinite}
  @keyframes breathe{0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(110,231,199,.5)}50%{opacity:.55;box-shadow:0 0 0 6px rgba(110,231,199,0)}}
  .ekg{animation:scroll 3s linear infinite}
  @keyframes scroll{from{transform:translateX(0)}to{transform:translateX(-120px)}}
  .slidein{animation:slidein .35s ease}
  @keyframes slidein{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
  @media (prefers-reduced-motion:reduce){.breathe,.ekg,.slidein{animation:none}}
</style>
</head>
<body class="font-body text-txt antialiased">
<div class="max-w-6xl mx-auto px-4 sm:px-6 py-6">

  <!-- Header: identity + live heartbeat -->
  <header class="flex flex-wrap items-center gap-4 justify-between border-b border-line pb-5">
    <div class="flex items-center gap-3">
      <div class="w-9 h-9 rounded-lg bg-surface2 border border-line grid place-items-center">
        <span class="w-2.5 h-2.5 rounded-full bg-mint breathe" id="dot"></span>
      </div>
      <div>
        <div class="font-display font-700 text-lg leading-none tracking-tight">CHIEF</div>
        <div class="font-mono text-[11px] text-muted tracking-widest">OPS CONSOLE</div>
      </div>
    </div>
    <div class="flex items-center gap-5">
      <div class="hidden sm:block h-10 w-[120px] overflow-hidden opacity-70">
        <svg class="ekg" width="240" height="40" viewBox="0 0 240 40" fill="none">
          <path d="M0 20 H30 l6 -14 l8 28 l6 -14 H120 H150 l6 -14 l8 28 l6 -14 H240"
            stroke="#6EE7C7" stroke-width="1.5" fill="none"/>
        </svg>
      </div>
      <div class="text-right">
        <div class="font-display font-600 text-sm" id="status">—</div>
        <div class="font-mono text-[11px] text-muted" id="lastactive">memuat…</div>
      </div>
    </div>
  </header>

  <!-- Health stat tiles -->
  <section class="grid grid-cols-2 md:grid-cols-4 gap-3 mt-5" id="tiles"></section>
  <div id="errbar" class="hidden mt-3 rounded-lg border border-coral/40 bg-coral/10 px-4 py-2.5 text-sm text-coral font-mono"></div>

  <!-- Main grid -->
  <div class="grid lg:grid-cols-5 gap-4 mt-4">
    <!-- Activity feed -->
    <div class="lg:col-span-3">
      <div class="rounded-xl bg-surface border border-line h-full">
        <div class="flex items-center gap-2 px-4 py-3 border-b border-line">
          <span class="w-1.5 h-1.5 rounded-full bg-mint breathe"></span>
          <h2 class="font-display font-600 text-sm tracking-tight">Aktivitas langsung</h2>
        </div>
        <div id="feed" class="p-3 space-y-1 max-h-[62vh] overflow-y-auto"></div>
      </div>
    </div>

    <!-- Right column: memory + chat -->
    <div class="lg:col-span-2 space-y-4">
      <div class="rounded-xl bg-surface border border-line">
        <div class="px-4 py-3 border-b border-line">
          <h2 class="font-display font-600 text-sm tracking-tight">Yang dia tahu</h2>
          <p class="text-[11px] text-muted mt-0.5">Fakta & preferensi soal kamu</p>
        </div>
        <div id="memory" class="p-3 space-y-1.5 max-h-[30vh] overflow-y-auto"></div>
      </div>
      <div class="rounded-xl bg-surface border border-line">
        <div class="px-4 py-3 border-b border-line">
          <h2 class="font-display font-600 text-sm tracking-tight">Riwayat obrolan</h2>
        </div>
        <div id="chat" class="p-3 space-y-2 max-h-[34vh] overflow-y-auto"></div>
      </div>
    </div>
  </div>

  <footer class="text-center text-[11px] text-muted font-mono mt-6">refresh tiap 3 dtk · read-only</footer>
</div>

<script>
const KIND = {
  tool:     {c:'azure', label:'tool',     dot:'#83A9F5'},
  proactive:{c:'amber', label:'proaktif', dot:'#F4B740'},
  reminder: {c:'amber', label:'reminder', dot:'#F4B740'},
  deploy:   {c:'mint',  label:'deploy',   dot:'#6EE7C7'},
  error:    {c:'coral', label:'error',    dot:'#F26D6D'},
};

function ago(ts){
  if(!ts) return '—';
  // event/message ts = UTC "YYYY-MM-DD HH:MM:SS"; facts = ISO lokal
  let d = new Date(ts.includes('T') ? ts : ts.replace(' ','T')+'Z');
  if(isNaN(d)) return ts;
  let s = Math.max(0,(Date.now()-d.getTime())/1000);
  if(s<60) return 'baru aja';
  if(s<3600) return Math.floor(s/60)+' mnt lalu';
  if(s<86400) return Math.floor(s/3600)+' jam lalu';
  return Math.floor(s/86400)+' hari lalu';
}
function esc(t){const e=document.createElement('div');e.textContent=t??'';return e.innerHTML;}

const STATUS = {
  awake:      {label:'AWAKE',      color:'text-mint',  dot:'bg-mint breathe'},
  restarting: {label:'RESTARTING', color:'text-amber', dot:'bg-amber breathe'},
  down:       {label:'TIDAK AKTIF',color:'text-coral', dot:'bg-coral'},
};
function render(s){
  const S = STATUS[s.status] || STATUS.down;
  const st=document.getElementById('status'), dot=document.getElementById('dot');
  st.textContent = S.label;
  st.className = 'font-display font-600 text-sm '+S.color;
  dot.className = 'w-2.5 h-2.5 rounded-full '+S.dot;
  document.getElementById('lastactive').textContent = 'aktivitas terakhir · '+ago(s.last_active);

  // tiles
  const tiles=[
    ['Pesan total', s.stats.messages, 'txt'],
    ['Tool dipakai · 24j', s.stats.tools_24h, 'azure'],
    ['Reminder aktif', s.stats.reminders, 'amber'],
    ['Fakta diingat', s.stats.facts, 'mint'],
  ];
  document.getElementById('tiles').innerHTML = tiles.map(([l,v,c])=>`
    <div class="rounded-xl bg-surface border border-line px-4 py-3">
      <div class="font-mono text-[11px] text-muted">${l}</div>
      <div class="font-display font-700 text-2xl mt-1 text-${c}">${v}</div>
    </div>`).join('');

  // error bar
  const eb=document.getElementById('errbar');
  if(s.last_error){ eb.classList.remove('hidden'); eb.textContent='⚠ error terakhir · '+ago(s.last_error.ts)+' · '+s.last_error.detail; }
  else eb.classList.add('hidden');

  // feed
  const feed=document.getElementById('feed');
  feed.innerHTML = s.feed.length ? s.feed.map(e=>{
    const k=KIND[e.kind]||{c:'muted',label:e.kind,dot:'#7C8798'};
    return `<div class="slidein flex items-start gap-3 px-2 py-2 rounded-lg hover:bg-surface2">
      <span class="mt-1.5 w-1.5 h-1.5 rounded-full shrink-0" style="background:${k.dot}"></span>
      <div class="min-w-0 flex-1">
        <span class="font-mono text-[10px] uppercase tracking-wider text-${k.c}">${k.label}</span>
        <span class="text-sm break-words">${esc(e.detail)||'<span class="text-muted">—</span>'}</span>
      </div>
      <span class="font-mono text-[11px] text-muted shrink-0">${ago(e.ts)}</span>
    </div>`;
  }).join('') : '<div class="text-muted text-sm px-2 py-6 text-center">Belum ada aktivitas.</div>';

  // memory
  const mem=document.getElementById('memory');
  mem.innerHTML = s.memory.length ? s.memory.map(f=>`
    <div class="flex items-center gap-2 text-sm px-2 py-1.5 rounded-lg bg-surface2/60">
      <span class="font-mono text-[11px] text-mint">${esc(f.subject)}</span>
      <span class="font-mono text-[11px] text-muted">${esc(f.predicate)}</span>
      <span class="truncate">${esc(f.object)}</span>
    </div>`).join('') : '<div class="text-muted text-sm px-2 py-4 text-center">Belum ada fakta.</div>';

  // chat
  const chat=document.getElementById('chat');
  chat.innerHTML = s.chat.length ? s.chat.map(m=>{
    const me = m.role==='user';
    return `<div class="flex ${me?'justify-end':'justify-start'}">
      <div class="max-w-[85%] rounded-2xl px-3 py-2 text-sm ${me?'bg-azure/15 text-txt':'bg-surface2 text-txt'}">
        ${esc(m.content)}
      </div></div>`;
  }).join('') : '<div class="text-muted text-sm px-2 py-4 text-center">Belum ada obrolan.</div>';
  chat.scrollTop = chat.scrollHeight;
}

async function tick(){
  try{ const r=await fetch('/api/state',{cache:'no-store'}); if(r.ok) render(await r.json()); }
  catch(e){}
}
tick(); setInterval(tick, 3000);
</script>
</body>
</html>"""
