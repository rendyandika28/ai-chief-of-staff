"""Auto-apply tool. Navigates to job application forms, auto-detects fields,
fills them from the user profile, and submits.

Uses the browser tool internally for form interaction.
"""

import json
from typing import Optional
from pathlib import Path

from app.tools.base import Tool


FIELD_MAP = {
    # Label pattern -> profile key | static value
    "full.name|nama.lengkap|first.name": "full_name",
    "email|e-mail|email.address": "email",
    "phone|mobile|telp|no.hp|nomor.telepon": "phone",
    "linkedin|linkedin.url|linkedin.profile": "linkedin",
    "website|portfolio|personal.site": "website",
    "location|city|kota|address|alamat": "location",
    "summary|about|tell.us.about|cover.letter": "summary",
    "resume|cv|upload.resume|attach.resume": "__resume__",
    "skills|technologies|tech.stack": "__skills__",
    "experience|work.experience|years.of.experience": "__experience__",
    "education|degree|university|school": "__education__",
}


class AutoApplyTool(Tool):
    name = "auto_apply"
    description = (
        "Auto-fill job application forms. Commands: "
        "fill:<job_url>, submit, status"
    )

    def __init__(self, browser_tool=None, profile=None):
        self._browser = browser_tool
        self._profile = profile
        self._fields_found = []
        self._fields_filled = []
        self._fields_skipped = []

    def run(self, input: str = "", user_id: str = "") -> str:
        parts = input.strip().split(":", 1)
        cmd = parts[0].strip().lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "fill":
            return self._fill(arg)
        if cmd == "submit":
            return self._submit()
        if cmd == "status":
            return self._status()
        return "Commands: fill:<url>, submit, status"

    def _fill(self, url: str) -> str:
        if not self._browser:
            return "Error: browser tool required for auto-apply"
        if not self._profile:
            return "Error: profile not configured"

        contact = self._profile.contact()
        profile_raw = self._profile.raw()

        self._fields_found = []
        self._fields_filled = []
        self._fields_skipped = []

        try:
            self._browser.run(f"navigate:{url}")
            import time
            time.sleep(2)
            page_text = self._browser.run("content")
        except Exception as e:
            return f"Error loading page: {e}"

        lines = [f"Membuka: {url}\n"]

        for pattern, key in FIELD_MAP.items():
            if self._try_fill(pattern, key, contact, profile_raw):
                continue

        lines.append(f"  Terisi: {len(self._fields_filled)} field")
        if self._fields_filled:
            for f in self._fields_filled:
                lines.append(f"    [v] {f}")
        if self._fields_skipped:
            lines.append(f"  Dilewati: {len(self._fields_skipped)} field")
            for f in self._fields_skipped:
                lines.append(f"    [?] {f}")

        return "\n".join(lines)

    def _try_fill(self, pattern: str, key: str, contact: dict, profile: dict) -> bool:
        """Try to find and fill a field matching the pattern."""
        patterns = pattern.split("|")
        found = False

        for p in patterns:
            # ponytail: simple attribute selector scan via eval
            selector = self._find_field(p)
            if not selector:
                continue

            value = self._resolve_value(key, contact, profile)
            if not value:
                self._fields_skipped.append(key)
                return True

            try:
                self._browser.run(f"type:{selector}:{value}")
                self._fields_filled.append(f"{key} = {value[:50]}")
                found = True
                break
            except Exception:
                continue

        if not found:
            self._fields_skipped.append(key)
        return True

    def _find_field(self, label_pattern: str) -> Optional[str]:
        """Find a form field by label text pattern. Returns CSS selector."""
        js = (
            f"""(() => {{
                const labels = document.querySelectorAll('label, .label, .field-label, [class*=\"label\"]');
                for (const l of labels) {{
                    if (/{label_pattern}/i.test(l.textContent)) {{
                        const id = l.getAttribute('for');
                        if (id) return '#' + id;
                        const input = l.closest('.form-group, .field, [class*=\"field\"], [class*=\"form\"]');
                        if (input) {{
                            const el = input.querySelector('input, textarea, select');
                            if (el && el.id) return '#' + el.id;
                            if (el && el.name) return '[name=\"' + el.name + '\"]';
                        }}
                    }}
                }}
                const inputs = document.querySelectorAll('input, textarea, select');
                for (const el of inputs) {{
                    const label = el.closest('label') || document.querySelector('label[for=\"' + (el.id || '') + '\"]');
                    const text = (label?.textContent || '') + ' ' + (el.placeholder || '') + ' ' + (el.name || '') + ' ' + (el.getAttribute('aria-label') || '');
                    if (/{label_pattern}/i.test(text)) {{
                        if (el.id) return '#' + el.id;
                        if (el.name) return '[name=\"' + el.name + '\"]';
                    }}
                }}
                return null;
            }})()"""
        )

        try:
            result = self._browser.run(f"eval:{js}")
            if result and result != "None" and result.strip():
                return result.strip()
        except Exception:
            pass
        return None

    def _resolve_value(self, key: str, contact: dict, profile: dict) -> str:
        if key == "__resume__":
            path = profile.get("resume_path", "data/resume.pdf")
            if Path(path).exists():
                return path
            return ""

        if key == "__skills__":
            return ", ".join(profile.get("skills", [])[:10])

        if key == "__experience__":
            return profile.get("summary", "")

        if key == "__education__":
            edu = profile.get("education", {})
            return f"{edu.get('degree', '')} - {edu.get('school', '')}"

        return contact.get(key, "")

    def _submit(self) -> str:
        if not self._browser:
            return "Error: browser required"
        try:
            js = """(() => {
                const submit = document.querySelector('button[type=\"submit\"], input[type=\"submit\"], button:has-text(\"Submit\"), button:has-text(\"Apply\"), button:has-text(\"Kirim\"), button:has-text(\"Lamar\")');
                if (submit) { submit.click(); return 'submitted'; }
                return 'no submit button found';
            })()"""
            result = self._browser.run(f"eval:{js}")
            return f"Submit: {result}"
        except Exception as e:
            return f"Error: {e}"

    def _status(self) -> str:
        return (
            f"Fields terisi: {len(self._fields_filled)}\n"
            + "\n".join(f"  [v] {f}" for f in self._fields_filled)
            + f"\nFields dilewati: {len(self._fields_skipped)}\n"
            + "\n".join(f"  [?] {f}" for f in self._fields_skipped)
        )
