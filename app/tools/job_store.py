"""Job store — scoring CV-match, cover letter, retensi lowongan (jobs.json).

Satu-satunya jalur masuk lowongan: Chrome extension (folder extension/) via
POST /api/jobs/ingest di dashboard. Scraper server-side lama dihapus (2026-07-14)
— job hunt satu pintu biar clean. Modul ini murni store + scoring, gak fetch apa-apa.
"""

import json
import os
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.schema import extract_json


WIB = timezone(timedelta(hours=7))

# jobs.json hidup di MEMORY_DIR biar container dashboard (mount folder itu) bisa baca-tulis.
JOB_DB = Path(os.getenv("MEMORY_DIR", "memory")) / "jobs.json"
# job_status.json = stage tiap lowongan, DITULIS dashboard (tombol), single-writer.
STATUS_DB = Path(os.getenv("MEMORY_DIR", "memory")) / "job_status.json"
SAVED_CAP = 50    # max lowongan 'saved' (belum ditindak) yg disimpen
PRUNE_DAYS = 30   # umur lowongan saved sebelum di-cek masih hidup gak
ACTIONED = ("drafted", "applied", "interview", "offer", "rejected")  # keluar kuota, riwayat

MATCH_THRESHOLD = 75  # cuma simpan lowongan dgn match >= threshold (preferensi Rendy)
_LLM_BATCH = 8   # jumlah lowongan per panggilan LLM (hemat call)

# Stack inti CV Rendy — dicari di judul+deskripsi buat skor kedalaman.
_STACK = ("react", "vue", "angular", "svelte", "next", "nuxt", "typescript",
          "javascript", "tailwind", "scss", "vuetify", "bootstrap", "redux", "html", "css")
_STRONG_TITLE = ("frontend", "front end", "react", "vue", "angular", "svelte",
                 "next", "nuxt", "ui engineer", "ui developer")
_JUNIOR_RE = re.compile(r"\b(junior|intern(ship)?|graduate|trainee|entry.level)\b")


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "")


def load_jobs() -> list:
    if not JOB_DB.exists():
        return []
    try:
        return json.loads(JOB_DB.read_text())
    except json.JSONDecodeError:
        return []


def load_status() -> dict:
    """{ '<id>': {'stage': ..., 'updated_at': ...} } — ditulis dashboard."""
    if not STATUS_DB.exists():
        return {}
    try:
        return json.loads(STATUS_DB.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def stage_of(status: dict, job_id) -> str:
    return (status.get(str(job_id)) or {}).get("stage", "saved")


def _alive(url: str) -> bool:
    """Best-effort liveness. 404/410 = mati → False. Error lain → anggap hidup, JANGAN hapus."""
    if not url:
        return True
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:  # headers doang
            return r.status < 400
    except urllib.error.HTTPError as e:
        return e.code not in (404, 410)
    except Exception:
        return True


def _cv_blurb(profile: dict) -> str:
    """Ringkasan profil buat konteks penilaian LLM."""
    p = profile or {}
    prefs = p.get("job_preferences", {})
    return (
        f"Role: {p.get('role', 'Frontend Engineer')}\n"
        f"Ringkasan: {(p.get('summary') or '')[:300]}\n"
        f"Skills: {', '.join(p.get('skills', [])[:14])}\n"
        f"Role diincar: {', '.join(prefs.get('roles', []))}\n"
        f"Preferensi: {prefs.get('preferred_location', 'remote')}; {prefs.get('notes', '')}\n"
        f"Eligibility: {prefs.get('eligibility', '')}"
    )


def match_score(job: dict, profile: dict) -> int:
    """Skor 0–100 seberapa cocok lowongan sama profil Rendy. Deterministik & transparan:
    judul (sinyal terkuat) + kedalaman stack di judul/deskripsi + konteks remote/Indonesia,
    dikurangi penalti kalau junior/intern."""
    title = (job.get("title") or "").lower().replace("-", " ")
    text = title + " " + _strip_html(job.get("description") or "").lower()
    roles = [r.lower() for r in (profile or {}).get("job_preferences", {}).get("roles", [])]

    if any(k in title for k in _STRONG_TITLE) or any(r in title for r in roles):
        base = 74
    elif "web developer" in title:
        base = 52
    else:
        base = 38

    stack = min(20, sum(1 for s in _STACK if s in text) * 4)  # +4/term unik, cap 20

    loc = (job.get("location") or "").lower()
    if any(w in text for w in ("remote", "contract", "freelance")):
        ctx = 10
    elif "indonesia" in loc or "indonesia" in text:
        ctx = 8
    else:
        ctx = 6

    penalty = 18 if _JUNIOR_RE.search(title) else 0  # Rendy 5+ thn — junior kurang cocok
    return max(0, min(100, base + stack + ctx - penalty))


def llm_rescore(jobs: list, profile: dict, llm):
    """Tahap 2: LLM baca JD lawan CV, override skor + kasih alasan. In-place.
    Batch biar hemat call; gagal/parse-error → skor heuristik dipertahankan."""
    cv = _cv_blurb(profile)
    system = (
        "Kamu penilai kecocokan lowongan buat kandidat ini. Skor 0-100 seberapa cocok "
        "TIAP lowongan — pertimbangkan stack, level/seniority, remote/lokasi, tipe kontrak, "
        "DAN aturan eligibility di bawah.\n\n"
        "ATURAN ELIGIBILITY (WAJIB — kandidat WNI/Indonesia, TIDAK punya visa kerja negara lain):\n"
        "- REMOTE di luar Indonesia: cocok kalau terbuka worldwide / nerima kandidat Indonesia & "
        "gak butuh izin kerja lokal. Kalau JD kekunci region/negara (mis. 'US only', "
        "'must be authorized to work in X', 'EU residents only') → skor RENDAH (maks ~40).\n"
        "- ON-SITE di luar Indonesia: cocok HANYA kalau EKSPLISIT nyediain visa sponsorship/relokasi. "
        "Gak nyebut sponsorship → skor RENDAH (maks ~35).\n"
        "- Di Indonesia (remote/on-site): eligible normal.\n"
        "- Remote yg gak nyebut pembatasan apa pun → anggap worldwide, jangan dihukum.\n\n"
        "Balas HANYA JSON array: "
        '[{"i":<index>,"score":<0-100>,"reason":"<alasan singkat Bahasa Indonesia, max 12 kata>"}]'
        f"\n\nKANDIDAT:\n{cv}"
    )
    for s in range(0, len(jobs), _LLM_BATCH):
        batch = jobs[s:s + _LLM_BATCH]
        listing = "\n".join(
            f'[{i}] {j.get("title")} @ {j.get("company")} ({j.get("location")}) — '
            + _strip_html(j.get("description") or "")[:500].replace("\n", " ")
            for i, j in enumerate(batch)
        )
        try:
            raw = llm.chat(
                [{"role": "system", "content": system},
                 {"role": "user", "content": "LOWONGAN:\n" + listing}],
                max_tokens=700,
            )
            data = extract_json(raw)
            if not isinstance(data, list):
                continue
            for item in data:
                idx = item.get("i")
                if isinstance(idx, int) and 0 <= idx < len(batch):
                    sc = item.get("score")
                    if isinstance(sc, (int, float)):
                        batch[idx]["score"] = max(0, min(100, int(sc)))
                    batch[idx]["reason"] = str(item.get("reason", "")).strip()[:90]
        except Exception:
            continue  # LLM down / parse gagal → biarin skor heuristik


def build_cover_letter(job: dict, profile: dict) -> str:
    """Static template letter from profile data — no LLM, zero cost."""
    contact = profile.get("contact", {})
    summary = (profile.get("summary") or "")[:100]
    company = f" at {job['company']}" if job.get("company") else ""
    return (
        f"Dear Hiring Manager,\n\n"
        f"I'm writing to apply for the {job.get('title', 'the')} position{company}. "
        f"With 5+ years as Frontend Engineer (React, Vue, Next.js, TypeScript), "
        f"I've built production apps across edtech, banking, e-commerce, and digital identity. "
        f"I'm seeking a remote contract/freelance arrangement. {summary}\n\n"
        f"Portfolio: {contact.get('website', '')}\n"
        f"LinkedIn: {contact.get('linkedin', '')}\n\n"
        f"Best regards,\n{contact.get('full_name', 'Rendy Andika')}\n"
        f"{contact.get('email', '')} | {contact.get('phone', '')}\n"
    )


def _norm_url(url: str) -> str:
    """Kunci dedup URL: lowercase, buang query/fragment (tracking param beda-beda)."""
    return (url or "").lower().split("?")[0].split("#")[0].rstrip("/")


def save_jobs(jobs: list) -> list:
    """Simpen job baru, return list id yg beneran kesimpen (dedup di-skip).
    Dedup by URL kalau ada (LinkedIn URL unik per post; judul post bisa generik),
    fallback judul."""
    JOB_DB.parent.mkdir(parents=True, exist_ok=True)
    existing = load_jobs()
    seen_titles = {j.get("title", "").lower() for j in existing}
    seen_urls = {_norm_url(j.get("url")) for j in existing if j.get("url")}
    # id monotonik (max+1), BUKAN len — prune bisa bikin len < id tertinggi → tabrakan.
    next_id = max((j.get("id", -1) for j in existing), default=-1) + 1
    saved_ids = []
    for job in jobs:
        title = job.get("title", "")
        url_key = _norm_url(job.get("url"))
        if url_key:
            if url_key in seen_urls:
                continue
        elif title.lower() in seen_titles:
            continue
        existing.append({
            "id": next_id, "title": title,
            "company": job.get("company", ""), "location": job.get("location", ""),
            "url": job.get("url", ""), "source": job.get("source", ""),
            "email": job.get("email", ""),
            "scraped_at": job.get("scraped_at", ""), "reason": job.get("reason", ""),
            "score": job.get("score", 0),
        })
        seen_titles.add(title.lower())  # dedup dalam batch yg sama juga
        if url_key:
            seen_urls.add(url_key)
        saved_ids.append(next_id)
        next_id += 1
    existing = prune_and_cap(existing)
    JOB_DB.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
    return saved_ids


def prune_and_cap(jobs: list) -> list:
    """Retensi lowongan:
    - Yang udah ditindak (drafted/applied/...) → SELALU disimpen (riwayat).
    - 'saved' lewat PRUNE_DAYS → cek hidup: mati (404) buang, hidup keep + reset umur.
    - 'saved' dibatasi SAVED_CAP terbaik by score."""
    status = load_status()
    now = datetime.now(WIB)
    cutoff = (now - timedelta(days=PRUNE_DAYS)).isoformat()
    kept = []
    for j in jobs:
        if stage_of(status, j.get("id")) in ACTIONED:
            kept.append(j)
            continue
        if j.get("scraped_at") and j["scraped_at"] < cutoff:
            if not _alive(j.get("url", "")):
                continue                      # mati → hapus
            j["scraped_at"] = now.isoformat()  # hidup → reset umur
        kept.append(j)
    saved = sorted(
        (j for j in kept if stage_of(status, j.get("id")) not in ACTIONED),
        key=lambda x: x.get("score", 0), reverse=True,
    )[:SAVED_CAP]
    actioned = [j for j in kept if stage_of(status, j.get("id")) in ACTIONED]
    return actioned + saved


def _demo():
    """Self-check. `python -m app.tools.job_store`."""
    import tempfile
    global JOB_DB, load_status, _alive, SAVED_CAP

    # match_score: role frontend + stack + remote lolos ≥80; junior/off-stack gugur
    prof = {"job_preferences": {"roles": ["frontend engineer", "react developer"]}}
    strong = match_score({"title": "Senior Frontend Engineer",
                          "description": "React, Next.js, TypeScript, Tailwind. Remote.",
                          "location": "Worldwide"}, prof)
    weak = match_score({"title": "Junior Frontend Developer",
                        "description": "HTML, CSS basics", "location": "Onsite"}, prof)
    offstack = match_score({"title": "PHP Web Developer",
                            "description": "Laravel, MySQL", "location": "Remote"}, prof)
    assert strong >= 80 and weak < 80 and offstack < 80, (strong, weak, offstack)

    # save_jobs: dedup by URL kalau ada, fallback judul; return ids; email persist
    JOB_DB = Path(tempfile.mkdtemp()) / "jobs.json"
    ids1 = save_jobs([
        {"title": "Frontend Developer", "url": "https://x.com/posts/1?utm=a",
         "email": "hr@a.com", "score": 80},
        {"title": "Frontend Developer", "url": "https://x.com/posts/2", "score": 80},  # URL beda → simpen
    ])
    assert ids1 == [0, 1], ids1
    ids2 = save_jobs([
        {"title": "Beda Judul", "url": "https://x.com/posts/1", "score": 80},      # URL sama → skip
        {"title": "Frontend Developer", "url": "", "score": 80},                    # no URL, judul sama → skip
    ])
    assert ids2 == [], ids2
    assert load_jobs()[0]["email"] == "hr@a.com"

    # prune_and_cap: actioned disimpen; saved-lawas-mati dibuang; cap by score
    _alive = lambda u: False
    old = (datetime.now(WIB) - timedelta(days=60)).isoformat()
    fresh = datetime.now(WIB).isoformat()
    load_status = lambda: {"1": {"stage": "applied"}}
    res = prune_and_cap([
        {"id": 0, "scraped_at": old, "url": "x", "score": 90},   # saved+lawas+mati → buang
        {"id": 1, "scraped_at": old, "url": "x", "score": 50},   # applied → simpen
        {"id": 2, "scraped_at": fresh, "url": "", "score": 80},  # saved+fresh → simpen
    ])
    assert {j["id"] for j in res} == {1, 2}, res
    load_status = lambda: {}
    SAVED_CAP = 1
    res2 = prune_and_cap([
        {"id": 3, "scraped_at": fresh, "url": "", "score": 90},
        {"id": 4, "scraped_at": fresh, "url": "", "score": 70},
    ])
    assert {j["id"] for j in res2} == {3}, res2
    SAVED_CAP = 50
    assert "drafted" in ACTIONED

    # llm_rescore: override skor + reason dari JSON (LLM palsu)
    class _FakeLLM:
        def chat(self, messages, max_tokens=0):
            return 'ok: [{"i":0,"score":91,"reason":"cocok react/next remote"}]'
    jl = [{"title": "Frontend Engineer", "company": "X", "location": "Remote",
           "description": "React, Next.js", "score": 74, "reason": ""}]
    llm_rescore(jl, prof, _FakeLLM())
    assert jl[0]["score"] == 91 and "cocok" in jl[0]["reason"], jl

    print(f"OK: match(strong={strong},weak={weak},offstack={offstack}) · save/dedup · prune · llm-rescore")


if __name__ == "__main__":
    _demo()
