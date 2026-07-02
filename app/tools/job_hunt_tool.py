"""Job search tool — scrapes Google Jobs via Playwright, saves listings, supports detail view."""

import json
import re
import urllib.request
import urllib.error
from pathlib import Path

from app.tools.base import Tool

PLATFORMS = {
    "linkedin": "https://www.linkedin.com/jobs/search/?keywords={role}&location={loc}",
    "glints": "https://glints.com/id/opportunities/jobs/explore?keyword={role}&locationName={loc}",
    "indeed": "https://id.indeed.com/jobs?q={role}&l={loc}",
    "google": "https://www.google.com/search?q={role}+jobs+{loc}&ibp=htl;jobs",
    "wellfound": "https://wellfound.com/jobs?keywords={role}&location={loc}",
    "glassdoor": "https://www.glassdoor.com/Job/jobs.htm?sc.keyword={role}&sc.location={loc}",
    "jobstreet": "https://www.jobstreet.co.id/{role}-jobs/in-{loc}",
    "kalibrr": "https://www.kalibrr.com/id-ID/search?query={role}&location={loc}",
}

JOB_DB = Path("data/jobs.json")
JOB_DB.parent.mkdir(parents=True, exist_ok=True)


class JobHuntTool(Tool):
    name = "job_hunt"
    description = (
        "Cari lowongan. Commands: search:<role>|<lokasi>, detail:<index>, saved, apply:<index>"
    )

    def __init__(self):
        self._playwright = None
        self._browser = None

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
        if cmd == "detail":
            return self._detail(arg)
        if cmd == "saved":
            return self._saved()
        return "Commands: search:<role>|<location>, detail:<index>, saved"

    def _search(self, arg: str) -> str:
        role, _, location = arg.partition("|")
        role = role.strip()
        location = location.strip() or "Remote"

        if not role:
            return "Error: role required (contoh: search:frontend engineer|jakarta)"

        lines = [f"Lowongan '{role}' di '{location}':\n"]

        # Scrape Google Jobs via Playwright
        try:
            listings = self._scrape_google(role, location)
            if listings:
                saved = self._save_jobs(listings)
                lines.append(f"{len(saved)} lowongan ditemukan:\n")
                for i, job in enumerate(saved[-15:]):
                    idx = len(self._load_jobs()) - len(saved) + i
                    comp = job.get("company", "?")
                    loc = job.get("location", "")
                    lines.append(f"  [{idx}] {job['title']} — {comp}" + (f" ({loc})" if loc else ""))
            else:
                lines.append("Tidak ada hasil scraping. Link alternatif:\n")
        except Exception as e:
            lines.append(f"Scraping error: {e}\nLink alternatif:\n")

        # Add platform URLs as backup
        for platform, url_template in PLATFORMS.items():
            url = url_template.format(
                role=urllib.request.quote(role),
                loc=urllib.request.quote(location),
            )
            lines.append(f"  [{platform}] {url}")

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
            import time; time.sleep(2)
            content = page.inner_text("body")
        finally:
            page.close()

        jobs = []
        seen = set()

        # Parse Google Jobs listing format
        for line in content.split("\n"):
            line = line.strip()
            if not line or len(line) < 5 or len(line) > 150:
                continue
            if line.lower() in seen:
                continue
            # Filter: titles usually 5-80 chars, contain job-related keywords
            if any(w in line.lower() for w in ("engineer", "developer", "manager", "designer",
                   "analyst", "lead", "senior", "junior", "staff", "frontend", "backend",
                   "fullstack", "devops", "mobile", "data", "product", "software")):
                seen.add(line.lower())
                jobs.append({"title": line, "company": "", "location": location})

        return jobs[:20]

    def _load_jobs(self) -> list:
        if not JOB_DB.exists():
            return []
        try:
            return json.loads(JOB_DB.read_text())
        except json.JSONDecodeError:
            return []

    def _save_jobs(self, jobs: list) -> list:
        existing = self._load_jobs()
        next_id = len(existing)
        for job in jobs:
            existing.append({
                "id": next_id,
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "location": job.get("location", ""),
            })
            next_id += 1
        JOB_DB.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
        return jobs

    def _detail(self, arg: str) -> str:
        try:
            idx = int(arg)
        except ValueError:
            return "Error: index must be a number"
        jobs = self._load_jobs()
        if idx < 0 or idx >= len(jobs):
            return f"Index {idx} out of range (0-{len(jobs)-1})"
        j = jobs[idx]
        return (
            f"[{j['id']}] {j['title']}\n"
            f"  Company: {j.get('company', '?')}\n"
            f"  Location: {j.get('location', '?')}"
        )

    def _saved(self) -> str:
        jobs = self._load_jobs()
        if not jobs:
            return "Belum ada lowongan tersimpan."
        lines = [f"{len(jobs)} lowongan tersimpan:\n"]
        for j in jobs[-20:]:
            comp = j.get("company", "?")
            lines.append(f"  [{j['id']}] {j['title']}" + (f" — {comp}" if comp else ""))
        return "\n".join(lines)
