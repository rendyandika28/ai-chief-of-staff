"""Job search — scrapes Google Jobs, ranks by CV match, generates cover letters."""

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path


WIB = timezone(timedelta(hours=7))

PLATFORMS = {
    "linkedin": "https://www.linkedin.com/jobs/search/?keywords={role}&location={loc}",
    "glints": "https://glints.com/id/opportunities/jobs/explore?keyword={role}&locationName={loc}",
    "indeed": "https://id.indeed.com/jobs?q={role}&l={loc}",
    "google": "https://www.google.com/search?q={role}+jobs+{loc}&ibp=htl;jobs&tbs=qdr:w",
    "wellfound": "https://wellfound.com/jobs?keywords={role}&location={loc}",
}

JOB_DB = Path("data/jobs.json")
JOB_DB.parent.mkdir(parents=True, exist_ok=True)


class JobHuntTool:
    name = "job_hunt"
    description = (
        "Cari lowongan & report. Commands: search:<role>|<loc>, report:<role>|<loc>, "
        "mark_applied:<index>, detail:<index>, saved"
    )

    def __init__(self, profile=None):
        self._profile = profile

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
        new_jobs = [j for j in fresh if j["title"].lower() not in existing_titles]

        if not new_jobs:
            return f"0 lowongan baru '{role}' di {location}. Total {len(existing)} tersimpan."

        skills = self._profile.raw().get("skills", []) if self._profile else []
        for j in new_jobs:
            j["score"] = sum(1 for s in skills if s.lower() in j["title"].lower())
        new_jobs.sort(key=lambda j: j.get("score", 0), reverse=True)
        top = new_jobs[:10]

        now = datetime.now(WIB)
        for j in new_jobs:
            j["scraped_at"] = now.isoformat()
        self._save_jobs(new_jobs)

        contact = self._profile.contact() if self._profile else {}
        summary = self._profile.raw().get("summary", "")[:200] if self._profile else ""

        prefs = self._profile.raw().get("job_preferences", {}) if self._profile else {}
        pref_notes = prefs.get("notes", "")

        lines = [f"🔔 TOP {len(top)} lowongan '{role}' di {location}:\n"]
        for j in top:
            jurl = j.get("url") or f"https://www.google.com/search?q={urllib.request.quote(j['title'])}+apply"
            contract_note = " (Prefer contract/freelance remote — no BPJS)" if pref_notes else ""
            lines.append(
                f"{'─'*40}\n"
                f"📌 {j['title']}" + (f" — {j['company']}" if j.get('company') else "") + "\n"
                f"   {j.get('location','Remote')} | Score: {j['score']} | ID: [{j.get('id','?')}]\n"
                f"   URL: {jurl}{contract_note}\n\n"
                f"📝 Cover Letter:\n"
                f"Dear Hiring Manager,\n\n"
                f"I'm writing to apply for the {j['title']} position. "
                f"With 5+ years as Frontend Engineer (React, Vue, Next.js, TypeScript), "
                f"I've built production apps across edtech, banking, e-commerce, and digital identity. "
                f"I'm seeking a remote contract/freelance arrangement. "
                f"{summary[:100]}\n\n"
                f"Portfolio: {contact.get('website','')}\n"
                f"LinkedIn: {contact.get('linkedin','')}\n\n"
                f"Best regards,\n{contact.get('full_name','Rendy Andika')}\n"
                f"{contact.get('email','')} | {contact.get('phone','')}\n"
            )
        lines.append("Ketik 'mark_applied:<id>' setelah apply biar gak duplikat.")
        return "\n".join(lines)

    def _fetch_jobs(self, role: str, location: str) -> list:
        # Remotive API — remote-only, gratis, no key, data terstruktur (bukan scraping).
        url = "https://remotive.com/api/remote-jobs?search=" + urllib.request.quote(role)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())

        # Search Remotive longgar (bisa nyasar) — filter judul harus mengandung kata kunci role.
        words = [w for w in role.lower().split() if len(w) > 3]
        jobs = []
        for j in data.get("jobs", []):
            title = (j.get("title") or "").strip()
            if not title:
                continue
            if words and not any(w in title.lower() for w in words):
                continue
            jobs.append({
                "title": title,
                "company": (j.get("company_name") or "").strip(),
                "location": j.get("candidate_required_location") or "Remote",
                "url": j.get("url", ""),
            })
        return jobs[:20]

    def _load_jobs(self) -> list:
        if not JOB_DB.exists():
            return []
        try:
            return json.loads(JOB_DB.read_text())
        except json.JSONDecodeError:
            return []

    def _save_jobs(self, jobs: list):
        existing = self._load_jobs()
        existing_titles = {j.get("title", "").lower() for j in existing}
        next_id = len(existing)
        for job in jobs:
            if job.get("title", "").lower() in existing_titles:
                continue
            existing.append({
                "id": next_id, "title": job.get("title", ""),
                "company": job.get("company", ""), "location": job.get("location", ""),
                "url": job.get("url", ""), "scraped_at": job.get("scraped_at", ""),
                "score": job.get("score", 0), "status": job.get("status", ""),
            })
            next_id += 1
        JOB_DB.write_text(json.dumps(existing, indent=2, ensure_ascii=False))

    def _mark_applied(self, arg: str) -> str:
        try:
            idx = int(arg)
        except ValueError:
            return "Error: index must be number"
        jobs = self._load_jobs()
        if idx < 0 or idx >= len(jobs):
            return f"Index {idx} out of range"
        jobs[idx]["status"] = "applied"
        JOB_DB.write_text(json.dumps(jobs, indent=2, ensure_ascii=False))
        return f"✓ [{idx}] {jobs[idx]['title']} — marked applied"

    def _detail(self, arg: str) -> str:
        try:
            idx = int(arg)
        except ValueError:
            return "Error: index must be number"
        jobs = self._load_jobs()
        if idx < 0 or idx >= len(jobs):
            return f"Index {idx} out of range (0-{len(jobs)-1})"
        j = jobs[idx]
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
