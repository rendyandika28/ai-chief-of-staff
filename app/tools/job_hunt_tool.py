"""Job search — scrapes Google Jobs, ranks by CV match, generates cover letters."""

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.tools.base import Tool

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


class JobHuntTool(Tool):
    name = "job_hunt"
    description = (
        "Cari lowongan & report. Commands: search:<role>|<loc>, report:<role>|<loc>, "
        "mark_applied:<index>, detail:<index>, saved"
    )

    def __init__(self, profile=None):
        self._playwright = None
        self._browser = None
        self._profile = profile

    def _ensure_browser(self):
        if self._playwright is None:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
        if self._browser is None:
            self._browser = self._playwright.chromium.launch(headless=True, args=[
                "--no-sandbox", "--disable-setuid-sandbox"
            ])
        return self._browser

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
            listings = self._scrape_google(role, location)
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
            fresh = self._scrape_google(role, location)
        except Exception as e:
            return f"Scraping error: {e}"

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

        lines = [f"🔔 TOP {len(top)} lowongan '{role}' di {location}:\n"]
        for j in top:
            jurl = j.get("url") or f"https://www.google.com/search?q={urllib.request.quote(j['title'])}+apply"
            lines.append(
                f"{'─'*40}\n"
                f"📌 {j['title']}\n"
                f"   Score CV match: {j['score']} | ID: [{j.get('id','?')}]\n"
                f"   URL: {jurl}\n\n"
                f"📝 Cover Letter:\n"
                f"Dear Hiring Manager,\n\n"
                f"I'm writing to apply for the {j['title']} position. "
                f"With 5+ years as Frontend Engineer specializing in React, Vue, Next.js, TypeScript, "
                f"and payment integrations, I've delivered production apps serving thousands of users. "
                f"{summary}\n\n"
                f"I'd welcome the opportunity to discuss how my experience can contribute to your team.\n\n"
                f"Best regards,\n{contact.get('full_name','Rendy Andika')}\n"
                f"{contact.get('email','')} | {contact.get('phone','')}\n"
                f"{contact.get('linkedin','')}\n"
            )
        lines.append("Ketik 'mark_applied:<id>' setelah apply biar gak duplikat.")
        return "\n".join(lines)

    def _scrape_google(self, role: str, location: str) -> list:
        browser = self._ensure_browser()
        page = browser.new_page()
        page.set_viewport_size({"width": 1280, "height": 900})
        try:
            url = PLATFORMS["google"].format(
                role=urllib.request.quote(role),
                loc=urllib.request.quote(location),
            )
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            import time; time.sleep(3)
            content = page.inner_text("body")
            links_raw = page.evaluate("""() => {
                const links = document.querySelectorAll('a[href*="/jobs/"], a[href*="linkedin"], a[href*="glints"], a[href*="indeed"]');
                return Array.from(links).slice(0, 30).map(a => ({
                    text: a.textContent.trim(),
                    href: a.href
                }));
            }""")
            url_map = {}
            for l in links_raw:
                key = l.get("text", "")[:40].lower()
                if key:
                    url_map[key] = l.get("href", "")
        finally:
            page.close()

        jobs, seen = [], set()
        for line in content.split("\n"):
            line = line.strip()
            if not line or len(line) < 5 or len(line) > 120:
                continue
            lk = line.lower()
            if lk in seen:
                continue
            if any(w in lk for w in (
                "engineer", "developer", "manager", "designer", "analyst",
                "lead", "senior", "junior", "staff", "frontend", "backend",
                "fullstack", "devops", "mobile", "data", "product", "software"
            )):
                seen.add(lk)
                job_url = ""
                for key, href in url_map.items():
                    if line[:30].lower() in key or key in line[:30].lower():
                        job_url = href
                        break
                jobs.append({"title": line, "company": "", "location": location, "url": job_url})
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
