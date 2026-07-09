"""Job search — aggregates free remote-job APIs, ranks by CV match, cover letters."""

import html
import json
import os
import re
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.schema import extract_json


WIB = timezone(timedelta(hours=7))

PLATFORMS = {
    "linkedin": "https://www.linkedin.com/jobs/search/?keywords={role}&location={loc}",
    "glints": "https://glints.com/id/opportunities/jobs/explore?keyword={role}&locationName={loc}",
    "indeed": "https://id.indeed.com/jobs?q={role}&l={loc}",
    "google": "https://www.google.com/search?q={role}+jobs+{loc}&ibp=htl;jobs&tbs=qdr:w",
    "wellfound": "https://wellfound.com/jobs?keywords={role}&location={loc}",
}

# jobs.json lives in MEMORY_DIR so the read-only dashboard container (which mounts
# that folder) can serve it. Old data/jobs.json is abandoned — it was polluted with
# non-frontend false positives from the previous over-loose title filter.
JOB_DB = Path(os.getenv("MEMORY_DIR", "memory")) / "jobs.json"

# Judul diterima kalau ngandung salah satu sinyal frontend ini — dari stack CV Rendy
# (React/Vue/Next/Nuxt/TS). Sengaja TANPA "javascript"/"typescript" telanjang (terlalu
# generik → nyerep QA/backend) dan TANPA "fullstack" (role fullstack ≠ dominan frontend).
_FRONTEND_SYNS = (
    "front", "react", "vue", "next", "nuxt", "angular", "svelte", "tailwind",
    "ui engineer", "ui developer", "web developer",
)
# Judul yg nyebut stack backend = didominasi backend / fullstack berat → dibuang walau
# ada React-nya. \bjava\b gak kena "javascript" (ada boundary). "back end" dari hyphen-normalize.
_BACKEND_RE = re.compile(
    r"c#|c\+\+|\.net|\b(?:node(?:js)?|java|python|php|ruby|rails|laravel|django|"
    r"spring|elixir|golang|kotlin|scala|back\s?end)\b"
)
# Tag pencarian Jobicy — lebih luas dari filter judul (surface banyak kandidat);
# hasilnya tetap disaring sinyal frontend + blocklist backend di atas.
_JOBICY_TAGS = ("frontend", "react", "vue", "javascript", "typescript")
_GENERIC_WORDS = {"engineer", "developer", "senior", "junior", "staff", "lead", "the", "and", "remote"}


def _frontend_dominant(title_norm: str, keys: set) -> bool:
    """title_norm = judul lowercase, hyphen→spasi. True kalau ada sinyal frontend DAN
    gak nyebut stack backend (menyingkirkan fullstack yg berat backend)."""
    return any(k in title_norm for k in keys) and not _BACKEND_RE.search(title_norm)


def _fix_mojibake(s: str) -> str:
    """Perbaiki teks UTF-8 yg ke-double-encode (mis. lokasi RemoteOK 'Ø§Ù...' → 'الرياض').
    Cuma jalan kalau kedeteksi pola mojibake & round-trip latin1→utf8 sukses."""
    if not s or not any(c in s for c in "ÃÂØÙÐ"):
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


MATCH_THRESHOLD = 75  # cuma simpan lowongan dgn match >= threshold (preferensi Rendy)
_LLM_CAP = 30    # max kandidat teratas (by heuristik) yg di-rescore LLM — batesin biaya
_LLM_BATCH = 8   # jumlah lowongan per panggilan LLM (hemat call)


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

# Stack inti CV Rendy — dicari di judul+deskripsi buat skor kedalaman.
_STACK = ("react", "vue", "angular", "svelte", "next", "nuxt", "typescript",
          "javascript", "tailwind", "scss", "vuetify", "bootstrap", "redux", "html", "css")
_STRONG_TITLE = ("frontend", "front end", "react", "vue", "angular", "svelte",
                 "next", "nuxt", "ui engineer", "ui developer")
_JUNIOR_RE = re.compile(r"\b(junior|intern(ship)?|graduate|trainee|entry.level)\b")


_SOURCES = {
    "remotive.com": "Remotive", "remoteok.com": "RemoteOK", "arbeitnow.com": "Arbeitnow",
    "jobicy.com": "Jobicy", "weworkremotely.com": "WeWorkRemotely", "linkedin.com": "LinkedIn",
}


def _source_from_url(url: str) -> str:
    u = (url or "").lower()
    return next((name for dom, name in _SOURCES.items() if dom in u), "?")


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "")


def match_score(job: dict, profile: dict) -> int:
    """Skor 0–100 seberapa cocok lowongan sama profil Rendy. Deterministik & transparan:
    judul (sinyal terkuat) + kedalaman stack di judul/deskripsi + konteks remote/Indonesia,
    dikurangi penalti kalau junior/intern. Deskripsi bikin lebih akurat; tanpa itu skor
    jatuh ke judul aja."""
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


def build_cover_letter(job: dict, profile: dict) -> str:
    """Static template letter from profile data — no LLM, zero cost. Shared by the
    Telegram report and the dashboard so the wording stays in one place."""
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


class JobHuntTool:
    name = "job_hunt"
    description = (
        "Cari lowongan & report. Commands: search:<role>|<loc>, report:<role>|<loc>, "
        "mark_applied:<index>, detail:<index>, saved"
    )

    def __init__(self, profile=None, llm=None):
        self._profile = profile
        self._llm = llm  # fast_llm (Haiku) buat semantic match; opsional — fallback ke heuristik

    def _llm_rescore(self, jobs: list, profile: dict):
        """Tahap 2: Haiku baca JD lawan CV, override skor + kasih alasan. In-place.
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
                raw = self._llm.chat(
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

    def run(self, input: str = "", user_id: str = "") -> str:
        parts = input.strip().split(":", 1)
        cmd = parts[0].strip().lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "search":
            return self._search(arg)
        if cmd == "report":
            return self._report_top(arg)
        if cmd == "mark_applied":
            return self._mark_applied(arg)
        if cmd == "detail":
            return self._detail(arg)
        if cmd == "saved":
            return self._saved()
        return "Commands: search:<role>|<loc>, report:<role>|<loc>, mark_applied:<idx>, detail:<idx>, saved"

    def _search(self, arg: str) -> str:
        role, _, location = arg.partition("|")
        role = role.strip()
        location = location.strip() or "Remote"
        if not role:
            return "Error: role required"
        lines = [f"Lowongan '{role}' di '{location}':\n"]
        try:
            listings = self._fetch_jobs(role, location)
            if listings:
                self._save_jobs(listings)
                jobs = self._load_jobs()
                lines.append(f"{len(listings)} ditemukan. Gunakan 'report' untuk top match + cover letter.\n")
                for j in jobs[-10:]:
                    lines.append(f"  [{j['id']}] {j['title']}")
        except Exception as e:
            lines.append(f"Scraping error: {e}\n")
        for p, url in PLATFORMS.items():
            lines.append(f"  [{p}] {url.format(role=urllib.request.quote(role), loc=urllib.request.quote(location))}")
        return "\n".join(lines)

    def _report_top(self, arg: str) -> str:
        role, _, location = arg.partition("|")
        role = role.strip()
        location = location.strip() or "Remote"
        if not role:
            return "Error: role required"
        try:
            fresh = self._fetch_jobs(role, location)
        except Exception as e:
            return f"Gagal ambil lowongan: {e}"

        existing = self._load_jobs()
        existing_titles = {j.get("title", "").lower() for j in existing}
        # fresh udah di-skor & difilter >= MATCH_THRESHOLD, urut match tertinggi.
        new_jobs = [j for j in fresh if j["title"].lower() not in existing_titles]

        if not new_jobs:
            return (f"0 lowongan baru '{role}' (match ≥{MATCH_THRESHOLD}%) di {location}. "
                    f"Total {len(existing)} tersimpan.")

        top = new_jobs[:10]

        now = datetime.now(WIB)
        for j in new_jobs:
            j["scraped_at"] = now.isoformat()
        self._save_jobs(new_jobs)

        profile = self._profile.raw() if self._profile else {}

        lines = [f"🔔 TOP {len(top)} lowongan '{role}' di {location}:\n"]
        for j in top:
            jurl = j.get("url") or f"https://www.google.com/search?q={urllib.request.quote(j['title'])}+apply"
            lines.append(
                f"{'─'*40}\n"
                f"📌 {j['title']}" + (f" — {j['company']}" if j.get('company') else "") + "\n"
                f"   {j.get('location','Remote')} | Match: {j['score']}% | ID: [{j.get('id','?')}]\n"
                f"   URL: {jurl}\n\n"
                f"📝 Cover Letter:\n" + build_cover_letter(j, profile)
            )
        lines.append("Ketik 'mark_applied:<id>' setelah apply biar gak duplikat.")
        return "\n".join(lines)

    def _fetch_jobs(self, role: str, location: str) -> list:
        """Aggregate several free, no-key remote-job APIs. Remote-only, structured
        data (not HTML scraping). Merge, filter to role-relevant titles, dedup."""
        role_l = role.lower().replace("-", " ")
        primary = next((w for w in role_l.split() if w not in _GENERIC_WORDS and len(w) > 2), role_l)
        # accepted keywords: distinctive words from the role + frontend synonyms
        keys = {w for w in role_l.split() if w not in _GENERIC_WORDS and len(w) > 2} | set(_FRONTEND_SYNS)

        seen, out = set(), []
        for fetch in (self._from_remotive, self._from_remoteok, self._from_arbeitnow,
                      self._from_jobicy, self._from_wwr, self._from_linkedin):
            try:
                for j in fetch(primary):
                    title = _fix_mojibake((j.get("title") or "").strip())
                    if not title:
                        continue
                    tl = title.lower().replace("-", " ")
                    if not _frontend_dominant(tl, keys):
                        continue
                    dedup = (j.get("url") or "").strip().lower() or tl
                    if dedup in seen:
                        continue
                    seen.add(dedup)
                    jurl = (j.get("url") or "").strip()
                    out.append({
                        "title": title,
                        "company": _fix_mojibake((j.get("company") or "").strip()),
                        "location": _fix_mojibake(j.get("location") or "Remote"),
                        "url": jurl,
                        "source": _source_from_url(jurl),
                        "description": j.get("description") or "",  # transient, buat scoring — gak disimpen
                    })
            except Exception:
                continue  # satu sumber down != gagal total

        profile = self._profile.raw() if self._profile else {}
        # Tahap 1 (murah): skor heuristik semua kandidat.
        for j in out:
            j["score"] = match_score(j, profile)
            j["reason"] = ""
        # Tahap 2 (LLM): Haiku baca JD lawan CV buat N kandidat teratas — mutasi in-place.
        if self._llm:
            self._llm_rescore(sorted(out, key=lambda x: x["score"], reverse=True)[:_LLM_CAP], profile)
        # Filter final + buang deskripsi (transient, biar jobs.json ramping) + urut match tertinggi.
        scored = []
        for j in out:
            j.pop("description", None)
            if j["score"] >= MATCH_THRESHOLD:
                scored.append(j)
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:40]

    @staticmethod
    def _get(url: str):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    def _from_remotive(self, keyword: str) -> list:
        data = self._get("https://remotive.com/api/remote-jobs?search=" + urllib.request.quote(keyword))
        return [
            {"title": j.get("title"), "company": j.get("company_name"),
             "location": j.get("candidate_required_location") or "Remote", "url": j.get("url"),
             "description": j.get("description")}
            for j in data.get("jobs", [])
        ]

    def _from_remoteok(self, keyword: str) -> list:
        # returns ~100 latest across all roles; first element is metadata. Filter happens upstream.
        data = self._get("https://remoteok.com/api")
        return [
            {"title": j.get("position"), "company": j.get("company"),
             "location": j.get("location") or "Remote", "url": j.get("url"),
             "description": (j.get("description") or "") + " " + " ".join(j.get("tags", []))}
            for j in data if isinstance(j, dict) and j.get("position")
        ]

    def _from_arbeitnow(self, keyword: str) -> list:
        data = self._get("https://www.arbeitnow.com/api/job-board-api")
        return [
            {"title": j.get("title"), "company": j.get("company_name"),
             "location": j.get("location") or "Remote", "url": j.get("url"),
             "description": (j.get("description") or "") + " " + " ".join(j.get("tags", []))}
            for j in data.get("data", []) if j.get("remote")
        ]

    def _from_jobicy(self, keyword: str) -> list:
        # Loop beberapa tag stack (react/vue/ts/…) — tag = vocabulary tetap Jobicy.
        # Dedup lintas-tag ditangani _fetch_jobs. keyword diabaikan (tag CV-based).
        out = []
        for tag in _JOBICY_TAGS:
            try:
                data = self._get("https://jobicy.com/api/v2/remote-jobs?count=50&tag=" + tag)
            except Exception:
                continue
            out.extend(
                {"title": j.get("jobTitle"), "company": j.get("companyName"),
                 "location": j.get("jobGeo") or "Remote", "url": j.get("url"),
                 "description": j.get("jobExcerpt") or j.get("jobDescription")}
                for j in data.get("jobs", [])
            )
        return out

    def _from_linkedin(self, keyword: str) -> list:
        # Dua wilayah: Worldwide (remote only — gak relokasi ke LN) + Indonesia (semua,
        # incl. on-site Jakarta — kandang sendiri). Dedup lintas-wilayah di _fetch_jobs.
        jobs = []
        for location, remote_only in (("Worldwide", True), ("Indonesia", False)):
            try:
                jobs.extend(self._linkedin_search(keyword, location, remote_only))
            except Exception:
                continue  # satu wilayah 429/error != gagal dua-duanya
        return jobs

    def _linkedin_search(self, keyword: str, location: str, remote_only: bool) -> list:
        # Endpoint guest publik — HTML kartu job, no auth. f_WT=2 = remote.
        url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
               "keywords=" + urllib.request.quote(keyword) +
               "&location=" + urllib.request.quote(location) +
               ("&f_WT=2" if remote_only else "") + "&start=0")
        req = urllib.request.Request(url, headers={"User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120 Safari/537.36")})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", "ignore")

        def rx(pat, s):
            m = re.search(pat, s, re.S)
            return html.unescape(m.group(1).strip()) if m else ""

        jobs = []
        for li in body.split("<li>")[1:]:
            title = rx(r'base-search-card__title">(.*?)<', li)
            if not title:
                continue
            jobs.append({
                "title": title,
                "company": rx(r'base-search-card__subtitle">(?:\s*<a[^>]*>)?(.*?)<', li),
                "location": rx(r'job-search-card__location">(.*?)<', li) or "Remote",
                "url": rx(r'href="(https://[a-z]{2,3}\.linkedin\.com/jobs/view/[^"?]+)', li),
            })
        return jobs

    def _from_wwr(self, keyword: str) -> list:
        # WeWorkRemotely = RSS, bukan JSON. Judul formatnya "Company: Job Title".
        req = urllib.request.Request(
            "https://weworkremotely.com/categories/remote-programming-jobs.rss",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            root = ET.fromstring(r.read())
        jobs = []
        for item in root.iter("item"):
            raw_title = (item.findtext("title") or "").strip()
            company, sep, title = raw_title.partition(": ")
            jobs.append({
                "title": title if sep else raw_title,
                "company": company if sep else "",
                "location": item.findtext("region") or "Remote",
                "url": item.findtext("link"),
                "description": item.findtext("description"),
            })
        return jobs

    def _load_jobs(self) -> list:
        if not JOB_DB.exists():
            return []
        try:
            return json.loads(JOB_DB.read_text())
        except json.JSONDecodeError:
            return []

    def _save_jobs(self, jobs: list):
        JOB_DB.parent.mkdir(parents=True, exist_ok=True)
        existing = self._load_jobs()
        existing_titles = {j.get("title", "").lower() for j in existing}
        # id monotonik (max+1), BUKAN len — prune bisa bikin len < id tertinggi → tabrakan.
        next_id = max((j.get("id", -1) for j in existing), default=-1) + 1
        for job in jobs:
            title = job.get("title", "")
            if title.lower() in existing_titles:
                continue
            existing.append({
                "id": next_id, "title": title,
                "company": job.get("company", ""), "location": job.get("location", ""),
                "url": job.get("url", ""), "source": job.get("source", ""),
                "scraped_at": job.get("scraped_at", ""), "reason": job.get("reason", ""),
                "score": job.get("score", 0), "status": job.get("status", ""),
            })
            existing_titles.add(title.lower())  # dedup dalam batch yg sama juga
            next_id += 1
        existing = self._prune(existing)
        JOB_DB.write_text(json.dumps(existing, indent=2, ensure_ascii=False))

    def _prune(self, jobs: list, days: int = 30) -> list:
        """Buang lowongan non-applied yg lebih tua dari `days` biar jobs.json gak numpuk.
        Yang applied & yang gak ada timestamp selalu disimpen (id tetap unik & stabil)."""
        cutoff = (datetime.now(WIB) - timedelta(days=days)).isoformat()
        return [
            j for j in jobs
            if j.get("status") == "applied" or not j.get("scraped_at") or j["scraped_at"] >= cutoff
        ]

    def _mark_applied(self, arg: str) -> str:
        try:
            jid = int(arg)
        except ValueError:
            return "Error: ID harus angka"
        jobs = self._load_jobs()
        job = next((j for j in jobs if j.get("id") == jid), None)
        if job is None:
            return f"ID {jid} gak ketemu"
        job["status"] = "applied"
        JOB_DB.write_text(json.dumps(jobs, indent=2, ensure_ascii=False))
        return f"✓ [{jid}] {job['title']} — marked applied"

    def _detail(self, arg: str) -> str:
        try:
            jid = int(arg)
        except ValueError:
            return "Error: ID harus angka"
        jobs = self._load_jobs()
        j = next((x for x in jobs if x.get("id") == jid), None)
        if j is None:
            return f"ID {jid} gak ketemu"
        return (
            f"[{j['id']}] {j['title']}\n"
            f"  Company: {j.get('company','?')}\n"
            f"  Location: {j.get('location','?')}\n"
            f"  URL: {j.get('url','?')}\n"
            f"  Status: {j.get('status','pending')}"
        )

    def _saved(self) -> str:
        jobs = self._load_jobs()
        if not jobs:
            return "Belum ada lowongan tersimpan."
        lines = [f"{len(jobs)} tersimpan:\n"]
        for j in jobs[-20:]:
            status = " ✓" if j.get("status") == "applied" else ""
            lines.append(f"  [{j['id']}] {j['title']}{status}")
        return "\n".join(lines)


def _demo():
    """Self-check filter dominan-frontend. `python -m app.tools.job_hunt_tool`."""
    keys = set(_FRONTEND_SYNS)
    n = lambda s: s.lower().replace("-", " ")
    keep = [
        "Frontend Engineer React and AWS",
        "Senior React Native Developer",
        "Front-End Developer",
        "Frontend Web Developer React/Typescript (Remote)",
        "Senior Frontend Software Engineer, Home Experience",
    ]
    drop = [
        "Senior Fullstack Engineer (Java / React)",       # java
        "Full-Stack Node.js and React Engineer",          # node
        "Senior Full-Stack Engineer (.NET/Angular)",      # .net
        "PHP Web Developer",                              # php
        "Laravel & React Engineer",                       # laravel
        "Software Engineer II, Full-Stack (Marketplace)", # no FE signal
        "Software Developer in Test (JavaScript)",        # QA, no FE signal
    ]
    for t in keep:
        assert _frontend_dominant(n(t), keys), f"harusnya KEEP: {t}"
    for t in drop:
        assert not _frontend_dominant(n(t), keys), f"harusnya DROP: {t}"
    # 'java' jangan kena 'javascript'
    assert _BACKEND_RE.search("java engineer") and not _BACKEND_RE.search("react javascript dev")

    # mojibake: teks rusak diperbaiki (simulasi double-encode), teks bersih gak disentuh
    arabic = "الرياض"
    broken = arabic.encode("utf-8").decode("latin-1")
    assert _fix_mojibake(broken) == arabic, _fix_mojibake(broken)
    assert _fix_mojibake("Worldwide") == "Worldwide"

    # prune: buang non-applied lawas, simpen applied lawas & yg fresh
    old = (datetime.now(WIB) - timedelta(days=60)).isoformat()
    fresh = datetime.now(WIB).isoformat()
    pruned = JobHuntTool()._prune([
        {"id": 0, "scraped_at": old, "status": ""},           # buang
        {"id": 1, "scraped_at": old, "status": "applied"},    # simpen (applied)
        {"id": 2, "scraped_at": fresh, "status": ""},         # simpen (fresh)
    ])
    assert {j["id"] for j in pruned} == {1, 2}, pruned

    # match_score: role frontend + stack + remote lolos ≥80; junior/off-stack gugur
    prof = {"job_preferences": {"roles": ["frontend engineer", "react developer"]}}
    strong = match_score({"title": "Senior Frontend Engineer",
                          "description": "React, Next.js, TypeScript, Tailwind. Remote.",
                          "location": "Worldwide"}, prof)
    weak = match_score({"title": "Junior Frontend Developer",
                        "description": "HTML, CSS basics", "location": "Onsite"}, prof)
    offstack = match_score({"title": "PHP Web Developer",
                            "description": "Laravel, MySQL", "location": "Remote"}, prof)
    assert strong >= 80, strong
    assert weak < 80, weak
    assert offstack < 80, offstack

    # LLM rescore: override skor + isi reason dari JSON balikan (pakai LLM palsu)
    class _FakeLLM:
        def chat(self, messages, max_tokens=0):
            return 'ok: [{"i":0,"score":91,"reason":"cocok react/next remote"}]'
    jl = [{"title": "Frontend Engineer", "company": "X", "location": "Remote",
           "description": "React, Next.js", "score": 74, "reason": ""}]
    JobHuntTool(llm=_FakeLLM())._llm_rescore(jl, prof)
    assert jl[0]["score"] == 91 and "cocok" in jl[0]["reason"], jl

    print(f"OK: filter {len(keep)} keep / {len(drop)} drop · mojibake · prune · "
          f"match(strong={strong},weak={weak},offstack={offstack}) · llm-rescore")


if __name__ == "__main__":
    _demo()
