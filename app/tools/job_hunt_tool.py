import json
import re
import urllib.request
import urllib.error
from pathlib import Path

from app.tools.base import Tool

JOB_DB = Path("data/jobs.json")
JOB_DB.parent.mkdir(parents=True, exist_ok=True)

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


class JobHuntTool(Tool):
    name = "job_hunt"
    description = (
        "Search jobs across platforms. Commands: "
        "search:<role>|<location>, "
        "detail:<index>, "
        "saved — list saved jobs."
    )

    def __init__(self):
        pass

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

        return (
            "Commands:\n"
            "  search:<role>|<location>  — search across platforms\n"
            "  detail:<index>  — view saved job details\n"
            "  saved  — list saved jobs\n\n"
            f"Platforms: {', '.join(PLATFORMS.keys())}"
        )

    def _search(self, arg):
        role, _, location = arg.partition("|")
        role = role.strip()
        location = location.strip() or "Remote"

        if not role:
            return "Error: role is required (e.g. search:frontend engineer|jakarta)"

        lines = [f"Lowongan '{role}' di '{location}':\n"]

        for platform, url_template in PLATFORMS.items():
            url = url_template.format(
                role=urllib.request.quote(role),
                loc=urllib.request.quote(location),
            )
            lines.append(f"[{platform}] {url}")

        lines.append("\nBuka link di atas untuk mencari lowongan.")

        return "\n".join(lines)

    def _parse_google_jobs(self, content):
        jobs = []
        # ponytail: loose regex parsing of Google Jobs result page
        # Match job title + company patterns
        pattern = re.compile(
            r'<h3[^>]*>(.*?)</h3>.*?<[^>]+>(.*?)</(?:div|span)>',
            re.DOTALL,
        )
        seen = set()
        for h3_match in re.finditer(r'<h3[^>]*>(.*?)</h3>', content, re.DOTALL):
            title = re.sub(r'<[^>]+>', '', h3_match.group(1)).strip()
            if not title or len(title) < 3 or len(title) > 100:
                continue
            if title.lower() in seen:
                continue
            seen.add(title.lower())
            jobs.append({"title": title, "company": "", "location": ""})
        return jobs

    def _save_jobs(self, jobs):
        existing = []
        if JOB_DB.exists():
            try:
                existing = json.loads(JOB_DB.read_text())
            except json.JSONDecodeError:
                pass

        next_id = len(existing)
        for job in jobs:
            existing.append({
                "id": next_id,
                "title": job["title"],
                "company": job.get("company", ""),
                "location": job.get("location", ""),
            })
            next_id += 1

        JOB_DB.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
        return jobs

    def _detail(self, arg):
        try:
            idx = int(arg)
        except ValueError:
            return f"Error: index must be a number (use 'saved' to list)"

        if not JOB_DB.exists():
            return "Belum ada lowongan tersimpan. Gunakan 'search' dulu."

        try:
            jobs = json.loads(JOB_DB.read_text())
        except json.JSONDecodeError:
            return "Error membaca data lowongan."

        if idx < 0 or idx >= len(jobs):
            return f"Index {idx} di luar range (0-{len(jobs)-1})"

        job = jobs[idx]
        return (
            f"[{job['id']}] {job['title']}\n"
            f"  Company: {job.get('company', '?')}\n"
            f"  Location: {job.get('location', '?')}\n"
        )

    def _saved(self):
        if not JOB_DB.exists():
            return "Belum ada lowongan tersimpan. Gunakan 'search:<role>|<location>'"

        try:
            jobs = json.loads(JOB_DB.read_text())
        except json.JSONDecodeError:
            return "Error membaca data lowongan."

        if not jobs:
            return "Belum ada lowongan tersimpan."

        lines = [f"{len(jobs)} lowongan tersimpan:\n"]
        for job in jobs[-20:]:
            lines.append(
                f"  [{job['id']}] {job['title']}"
                + (f" — {job['company']}" if job.get('company') else "")
            )
        return "\n".join(lines)
