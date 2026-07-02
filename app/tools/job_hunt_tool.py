"""Job search — scrapes Google Jobs via Playwright, diffs new listings, saves to DB."""

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
        "Cari lowongan. Commands: search:<role>|<lokasi>, diff:<role>|<lokasi>, detail:<index>, saved"
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
        if cmd == "diff":
            return self._diff_and_report(arg)
        if cmd == "apply":
            return self._apply(arg, user_id)
        if cmd == "detail":
            return self._detail(arg)
        if cmd == "saved":
            return self._saved()
        return "Commands: search:<role>|<location>, diff:<role>|<location>, apply:<index>, detail:<index>, saved"

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
                lines.append(f"{len(listings)} ditemukan:\n")
                for j in jobs[-15:]:
                    lines.append(f"  [{j['id']}] {j['title']} — {j.get('company','?')}")
            else:
                lines.append("Tidak ada hasil.\n")
        except Exception as e:
            lines.append(f"Scraping error: {e}\n")
        for p, url in PLATFORMS.items():
            lines.append(f"  [{p}] {url.format(role=urllib.request.quote(role), loc=urllib.request.quote(location))}")
        return "\n".join(lines)

    def _diff_and_report(self, arg: str) -> str:
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

        now = datetime.now(WIB)
        for job in fresh:
            job["scraped_at"] = now.isoformat()

        if new_jobs:
            self._save_jobs(new_jobs)
            lines = [f"🔔 {len(new_jobs)} lowongan BARU '{role}' di {location}:"]
            for j in new_jobs[:8]:
                lines.append(f"  - {j['title']}" + (f" — {j.get('company','')}" if j.get('company') else ""))
        return "\n".join(lines)

    def _apply(self, arg: str, user_id: str = "") -> str:
        """Navigate to job, find apply link, fill form from profile."""
        try:
            idx = int(arg)
        except ValueError:
            return "Error: index must be a number"

        jobs = self._load_jobs()
        if idx < 0 or idx >= len(jobs):
            return f"Index {idx} out of range (0-{len(jobs)-1})"

        job = jobs[idx]
        title = job.get("title", "")
        url = job.get("url", "")

        if not url:
            # Search Google for the job title + apply
            search_url = f"https://www.google.com/search?q={urllib.request.quote(title)}+apply"
            browser = self._ensure_browser()
            page = browser.new_page()
            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
                import time
                time.sleep(2)
                links = page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a[href*="linkedin"]'))
                        .map(a => a.href).slice(0, 3);
                }""")
                if not links:
                    links = page.evaluate("""() => {
                        return Array.from(document.querySelectorAll('a'))
                            .filter(a => /apply|job|career/i.test(a.href + a.textContent))
                            .map(a => a.href).slice(0, 3);
                    }""")
                if links:
                    url = links[0]
            finally:
                page.close()

        if not url:
            return f"Tidak bisa menemukan link apply untuk: {title}\nCari manual: {search_url}"

        # Navigate to apply page
        browser = self._ensure_browser()
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            import time
            time.sleep(3)

            # Try to find and fill form fields
            filled = []
            if self._profile:
                contact = self._profile.contact()
                # Try common field patterns
                field_map = {
                    "full_name": [r'full.?name', r'nama.?lengkap', r'first.?name'],
                    "email": [r'email', r'e-mail'],
                    "phone": [r'phone', r'mobile', r'telp'],
                    "website": [r'website', r'portfolio', r'linkedin'],
                }
                for key, patterns in field_map.items():
                    value = contact.get(key, "")
                    if not value:
                        continue
                    for pat in patterns:
                        try:
                            page.fill(f"input[name*='{pat}' i]", value, timeout=2000)
                            filled.append(key)
                            break
                        except Exception:
                            pass

            return (
                f"Mencoba apply: [{job['id']}] {title}\n"
                f"URL: {url}\n"
                + (f"Field terisi: {', '.join(filled)}\n" if filled else "")
                + "Silakan cek dan submit manual. Ketik 'apply submit' untuk submit otomatis."
            )
        finally:
            page.close()
        return f"Scraping {now.strftime('%H:%M')}: 0 lowongan baru. Total {len(existing)} tersimpan."

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
            import time
            time.sleep(2)
            content = page.inner_text("body")
        finally:
            page.close()

        jobs = []
        seen = set()
        for line in content.split("\n"):
            line = line.strip()
            if not line or len(line) < 5 or len(line) > 120:
                continue
            if line.lower() in seen:
                continue
            if any(w in line.lower() for w in (
                "engineer", "developer", "manager", "designer", "analyst",
                "lead", "senior", "junior", "staff", "frontend", "backend",
                "fullstack", "devops", "mobile", "data", "product", "software"
            )):
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
                "scraped_at": job.get("scraped_at", ""),
            })
            next_id += 1
        JOB_DB.write_text(json.dumps(existing, indent=2, ensure_ascii=False))

    def _detail(self, arg: str) -> str:
        try:
            idx = int(arg)
        except ValueError:
            return "Error: index must be a number"
        jobs = self._load_jobs()
        if idx < 0 or idx >= len(jobs):
            return f"Index {idx} out of range (0-{len(jobs)-1})"
        j = jobs[idx]
        return f"[{j['id']}] {j['title']}\n  Company: {j.get('company','?')}\n  Location: {j.get('location','?')}"

    def _saved(self) -> str:
        jobs = self._load_jobs()
        if not jobs:
            return "Belum ada lowongan tersimpan."
        lines = [f"{len(jobs)} tersimpan:\n"]
        for j in jobs[-20:]:
            lines.append(f"  [{j['id']}] {j['title']}" + (f" — {j.get('company','')}" if j.get('company') else ""))
        return "\n".join(lines)
