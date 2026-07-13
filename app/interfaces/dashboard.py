"""Read-only monitoring dashboard for the AI agent.

Reads the same SQLite files the bot writes (MEMORY_DIR, mounted read-only in the
Coolify container). Serves one polished page that polls /api/state. Standalone:
imports only stdlib + fastapi + app.lib.events, so the container stays light.

Run:  uv run uvicorn app.interfaces.dashboard:app --host 0.0.0.0 --port 8000
Auth: HTTP Basic via DASH_USER / DASH_PASS env vars.
"""

import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone

from fastapi import Body, Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.lib.events import recent
from app.lib.usage import today as usage_today
from app.tools import gmail_draft
from app.tools.job_store import (
    JOB_DB, STATUS_DB, MATCH_THRESHOLD, WIB, build_cover_letter, llm_rescore, match_score,
    save_jobs,
)

MEMORY_DIR = os.getenv("MEMORY_DIR", "memory")
STAGES = ("saved", "drafted", "applied", "interview", "offer", "rejected")  # 'saved' = default (gak ada entri)


def _load_jobs() -> list:
    try:
        return json.loads(JOB_DB.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def _load_status() -> dict:
    try:
        return json.loads(STATUS_DB.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _stage_of(status: dict, job_id) -> str:
    return (status.get(str(job_id)) or {}).get("stage", "saved")


def _load_profile() -> dict:
    try:
        with open(os.path.join(MEMORY_DIR, "profile.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

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
        "usage": usage_today(),  # {in_tokens, out_tokens, cost_usd, by_model}
    }


@app.get("/api/state")
def api_state(_: bool = Depends(auth)):
    return JSONResponse(build_state())


@app.get("/api/jobs")
def api_jobs(_: bool = Depends(auth)):
    # match tertinggi dulu, lalu terbaru (id) sebagai tie-break
    jobs = sorted(_load_jobs(), key=lambda j: (j.get("score", 0), j.get("id", 0)), reverse=True)
    status = _load_status()
    for j in jobs:
        j["stage"] = _stage_of(status, j.get("id"))
    counts = {s: sum(1 for j in jobs if j["stage"] == s) for s in STAGES}
    return JSONResponse({"total": len(jobs), "jobs": jobs, "counts": counts})


@app.post("/api/jobs/{job_id}/stage")
def api_set_stage(job_id: int, payload: dict = Body(...), _: bool = Depends(auth)):
    stage = (payload or {}).get("stage", "")
    if stage not in STAGES:
        raise HTTPException(400, "stage gak valid")
    if job_id not in {j.get("id") for j in _load_jobs()}:
        raise HTTPException(404, "lowongan gak ada")
    try:
        _write_stage(job_id, stage)
    except OSError:
        raise HTTPException(500, "gagal nulis status — mount dashboard masih read-only?")
    return JSONResponse({"ok": True, "stage": stage})


@app.get("/api/cover_letter/{job_id}")
def api_cover_letter(job_id: int, _: bool = Depends(auth)):
    job = next((j for j in _load_jobs() if j.get("id") == job_id), None)
    if job is None:
        raise HTTPException(404, "Lowongan gak ketemu")
    return JSONResponse({"text": build_cover_letter(job, _load_profile())})


# ── Extension API (LinkedIn Job Assist) ─────────────────────────────────────
# Spec: docs/superpowers/specs/2026-07-13-linkedin-job-assist-design.md

_basic_optional = HTTPBasic(auto_error=False)


def ext_auth(x_api_token: str = Header(""),
             cred: HTTPBasicCredentials = Depends(_basic_optional)) -> bool:
    """Token extension (X-API-Token) ATAU basic auth dashboard — dua-duanya sah.
    Extension pake token; tombol di dashboard pake sesi basic yang udah ada."""
    tok = os.getenv("EXT_API_TOKEN", "")
    if tok and x_api_token and secrets.compare_digest(x_api_token, tok):
        return True
    if cred is not None:
        return auth(cred)  # raise 401 sendiri kalau salah
    if not tok:
        raise HTTPException(503, "EXT_API_TOKEN belum di-set di environment")
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token invalid")


def _fast_llm():
    """GroqLLM buat rescore — lazy, opsional. Gak ada key/lib → None (heuristik doang)."""
    try:
        from app.llm.groq import GroqLLM
        llm = GroqLLM()
        return llm if llm._client else None
    except Exception:
        return None


def _write_stage(job_id: int, stage: str):
    ids = {j.get("id") for j in _load_jobs()}
    status_map = {k: v for k, v in _load_status().items() if k.isdigit() and int(k) in ids}
    if stage == "saved":
        status_map.pop(str(job_id), None)  # balik default = hapus entri
    else:
        status_map[str(job_id)] = {"stage": stage, "updated_at": datetime.now(timezone.utc).isoformat()}
    STATUS_DB.write_text(json.dumps(status_map, indent=2, ensure_ascii=False))


@app.post("/api/jobs/ingest")
def api_ingest(payload: dict = Body(...), _: bool = Depends(ext_auth)):
    """Batch dari extension: skor semua, simpen yang >= threshold. Anti asal-apply."""
    items = (payload or {}).get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "items kosong")
    profile = _load_profile()
    now = datetime.now(WIB).isoformat()
    jobs = [{
        "title": (it.get("title") or "").strip()[:200],
        "company": (it.get("company") or "").strip()[:120],
        "location": (it.get("location") or "Remote").strip()[:120],
        "url": (it.get("url") or "").strip(),
        "email": (it.get("email") or "").strip()[:200],
        "source": "linkedin-ext",
        "description": (it.get("body") or "")[:4000],
        "scraped_at": now, "reason": "",
    } for it in items[:100] if (it.get("title") or "").strip()]

    for j in jobs:
        j["score"] = match_score(j, profile)
    llm = _fast_llm()
    if llm:
        llm_rescore(jobs, profile, llm)  # gagal → skor heuristik bertahan

    auto_draft = bool((payload or {}).get("auto_draft"))
    results = []
    for j in jobs:
        j.pop("description", None)  # transient, gak disimpen
        if j["score"] < MATCH_THRESHOLD:
            results.append({"url": j["url"], "stored": False, "score": j["score"], "job_id": None})
            continue
        # per-job save biar dapet mapping url→id (dedup di dalem save_jobs by URL)
        saved = save_jobs([j])
        res = {"url": j["url"], "stored": bool(saved), "score": j["score"],
               "job_id": saved[0] if saved else None}
        # Auto-draft: langsung bikin Gmail draft buat yang baru kesimpen & ber-email —
        # extension cuma kirim post ber-email (cold approach), Rendy tinggal buka Gmail.
        if auto_draft and saved and j.get("email"):
            draft_id, err = _draft_job(saved[0])
            res["drafted"] = draft_id is not None
            if err:
                res["draft_error"] = err[1]
        results.append(res)
    return JSONResponse({"results": results})


def _draft_job(job_id: int) -> tuple:
    """Bikin Gmail draft buat 1 job tersimpan. Return (draft_id, None) atau (None, (code, pesan)).
    Dipake endpoint /api/drafts DAN auto-draft di ingest."""
    jobs = _load_jobs()
    job = next((j for j in jobs if j.get("id") == job_id), None)
    if job is None:
        return None, (404, "lowongan gak ada")
    if not job.get("email"):
        return None, (400, "job ini gak punya email — apply manual via URL")
    if _stage_of(_load_status(), job_id) == "drafted":
        return None, (409, "udah didraft — cek folder Draft Gmail")

    profile = _load_profile()
    try:
        draft_id = gmail_draft.create_draft(
            to=job["email"],
            subject=gmail_draft.subject_for(job, profile),
            body=build_cover_letter(job, profile),
            cv_path=profile.get("resume_path", ""),
        )
    except FileNotFoundError:
        return None, (502, "token Gmail belum ada — jalanin scripts/google_auth.py pribadi gmail")
    except Exception as e:
        return None, (502, f"Gmail API gagal: {e}")  # job utuh, bisa apply manual

    job["gmail_draft_id"] = draft_id
    try:
        JOB_DB.write_text(json.dumps(jobs, indent=2, ensure_ascii=False))
        _write_stage(job_id, "drafted")
    except OSError:
        return None, (500, "draft kebuat tapi gagal nulis status — mount read-only?")
    return draft_id, None


@app.post("/api/drafts")
def api_create_draft(payload: dict = Body(...), _: bool = Depends(ext_auth)):
    """Bikin Gmail draft (cover letter + CV) buat 1 job. Semi-auto: Rendy Send manual."""
    job_id = (payload or {}).get("job_id")
    if not isinstance(job_id, int):
        raise HTTPException(400, "job_id harus angka")
    draft_id, err = _draft_job(job_id)
    if err:
        raise HTTPException(err[0], err[1])
    return JSONResponse({"ok": True, "job_id": job_id, "gmail_draft_id": draft_id})


@app.get("/api/drafts")
def api_list_drafts(_: bool = Depends(ext_auth)):
    status_map = _load_status()
    drafts = [dict(j, stage="drafted") for j in _load_jobs()
              if _stage_of(status_map, j.get("id")) == "drafted"]
    return JSONResponse({"total": len(drafts), "drafts": drafts})


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
    mint:'#6EE7C7', amber:'#F4B740', coral:'#F26D6D', azure:'#83A9F5', lilac:'#B79DF0',
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

  <!-- Tabs -->
  <nav class="flex gap-1 mt-5 border-b border-line" id="tabs">
    <button data-tab="ops" class="tab px-4 py-2 text-sm font-display font-600 border-b-2 border-mint text-txt">Ops</button>
    <button data-tab="jobs" class="tab px-4 py-2 text-sm font-display font-600 border-b-2 border-transparent text-muted">Lowongan</button>
  </nav>

  <!-- View: Ops -->
  <div id="view-ops">

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
  </div><!-- /view-ops -->

  <!-- View: Lowongan -->
  <div id="view-jobs" class="hidden mt-4">
    <div class="rounded-xl bg-surface border border-line">
      <div class="px-4 py-3 border-b border-line">
        <h2 class="font-display font-600 text-sm tracking-tight">Lowongan tersimpan</h2>
        <p class="text-[11px] text-muted mt-0.5" id="jobcount">memuat…</p>
      </div>
      <div id="jobs" class="p-3 space-y-2 max-h-[70vh] overflow-y-auto"></div>
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
    ['Biaya hari ini', '$'+(s.usage?s.usage.cost_usd.toFixed(2):'0.00'), 'txt'],
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

// ---- Lowongan ----
function matchColor(s){ return s>=90?'mint':s>=75?'amber':'muted'; }
const STAGES = ['saved','drafted','applied','interview','offer','rejected'];
const STAGE_ON = {
  saved:'bg-line text-txt border-muted/50',
  drafted:'bg-lilac/20 text-lilac border-lilac/50',
  applied:'bg-azure/20 text-azure border-azure/50',
  interview:'bg-amber/20 text-amber border-amber/50',
  offer:'bg-mint/20 text-mint border-mint/50',
  rejected:'bg-coral/20 text-coral border-coral/50',
};
async function setStage(id, stage){
  try{
    const r = await fetch(`/api/jobs/${id}/stage`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({stage})});
    if(!r.ok){ const e=await r.json().catch(()=>({})); alert(e.detail||'gagal update stage'); return; }
    loadJobs();
  }catch(e){ alert('gagal update stage'); }
}
function jobCard(j){
  const url = j.url || ('https://www.google.com/search?q='+encodeURIComponent(j.title)+'+apply');
  const score = j.score ?? 0;
  const c = matchColor(score);
  const stage = j.stage || 'saved';
  const buttons = STAGES.map(s=>`<button onclick="setStage(${j.id},'${s}')" class="px-2 py-0.5 rounded border text-[10px] font-mono capitalize ${stage===s?STAGE_ON[s]:'text-muted border-line hover:bg-surface2'}">${s}</button>`).join('');
  return `<div class="rounded-lg bg-surface2/60 border border-line px-3 py-2.5">
    <div class="flex items-start gap-3">
      <div class="shrink-0 w-11 text-center">
        <div class="font-display font-700 text-lg leading-none text-${c}">${score}<span class="text-[10px]">%</span></div>
        <div class="font-mono text-[9px] text-muted tracking-wider mt-0.5">MATCH</div>
      </div>
      <div class="min-w-0 flex-1">
        <div class="text-sm font-600 break-words">${esc(j.title)}</div>
        <div class="text-[12px] text-muted mt-0.5">${esc(j.company)||'—'} · ${esc(j.location)||'Remote'}</div>
        ${j.source?`<span class="inline-block mt-1 px-1.5 py-0.5 rounded bg-azure/15 text-azure font-mono text-[10px] tracking-wide">via ${esc(j.source)}</span>`:''}
        ${j.reason?`<div class="text-[11px] text-muted italic mt-1">💡 ${esc(j.reason)}</div>`:''}
      </div>
      <div class="flex flex-col items-end gap-1 shrink-0">
        <a href="${esc(url)}" target="_blank" rel="noopener" class="text-[11px] font-mono text-azure hover:underline">apply ↗</a>
        <button onclick="toggleCover(${j.id}, this)" class="text-[11px] font-mono text-mint hover:underline">cover letter</button>
        ${j.email && stage!=='drafted' ? `<button onclick="draftEmail(${j.id}, this)" class="text-[11px] font-mono text-lilac hover:underline">✉ draft email</button>`:''}
        ${j.gmail_draft_id ? `<span class="text-[10px] font-mono text-lilac/70">di Draft Gmail ✓</span>`:''}
      </div>
    </div>
    <div class="flex flex-wrap gap-1 mt-2">${buttons}</div>
    <pre id="cl-${j.id}" class="hidden mt-2 p-3 rounded-lg bg-ink border border-line text-[12px] text-txt font-mono whitespace-pre-wrap overflow-x-auto"></pre>
  </div>`;
}
async function loadJobs(){
  try{
    const r = await fetch('/api/jobs',{cache:'no-store'}); if(!r.ok) return;
    const d = await r.json();
    const c = d.counts||{};
    document.getElementById('jobcount').textContent =
      `${d.total} lowongan · Saved ${c.saved||0} · Drafted ${c.drafted||0} · Applied ${c.applied||0} · Interview ${c.interview||0} · Offer ${c.offer||0} · Rejected ${c.rejected||0}`;
    const box = document.getElementById('jobs');
    box.innerHTML = d.jobs.length ? d.jobs.map(jobCard).join('')
      : '<div class="text-muted text-sm px-2 py-6 text-center">Belum ada lowongan.</div>';
  }catch(e){}
}
async function draftEmail(id, btn){
  btn.disabled = true; btn.textContent = 'membuat…';
  try{
    const r = await fetch('/api/drafts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:id})});
    if(!r.ok){ const e=await r.json().catch(()=>({})); alert(e.detail||'gagal bikin draft'); btn.disabled=false; btn.textContent='✉ draft email'; return; }
    loadJobs(); // stage → drafted, tombol ilang, badge muncul
  }catch(e){ alert('gagal bikin draft'); btn.disabled=false; btn.textContent='✉ draft email'; }
}
async function toggleCover(id, btn){
  const pre = document.getElementById('cl-'+id);
  if(!pre.classList.contains('hidden')){ pre.classList.add('hidden'); return; }
  pre.classList.remove('hidden');
  if(pre.dataset.loaded) return;
  pre.textContent = 'memuat…';
  try{
    const r = await fetch('/api/cover_letter/'+id,{cache:'no-store'});
    pre.textContent = r.ok ? (await r.json()).text : 'gagal memuat';
    if(r.ok) pre.dataset.loaded = '1';
  }catch(e){ pre.textContent = 'gagal memuat'; }
}
document.querySelectorAll('#tabs .tab').forEach(b=>b.addEventListener('click',()=>{
  const t=b.dataset.tab;
  document.querySelectorAll('#tabs .tab').forEach(x=>{
    const on = x.dataset.tab===t;
    x.className='tab px-4 py-2 text-sm font-display font-600 border-b-2 '+(on?'border-mint text-txt':'border-transparent text-muted');
  });
  document.getElementById('view-ops').classList.toggle('hidden', t!=='ops');
  document.getElementById('view-jobs').classList.toggle('hidden', t!=='jobs');
  if(t==='jobs') loadJobs();
}));

async function tick(){
  try{ const r=await fetch('/api/state',{cache:'no-store'}); if(r.ok) render(await r.json()); }
  catch(e){}
}
tick(); setInterval(tick, 3000);
</script>
</body>
</html>"""
