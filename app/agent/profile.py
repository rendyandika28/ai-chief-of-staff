import json
from pathlib import Path


class Profile:
    def __init__(self, path: str = "memory/profile.json"):
        self.path = Path(path)
        self._data = None

    def _read(self):
        if self._data is None:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        return self._data

    def load(self) -> str:
        """Formatted text for LLM prompt injection."""
        data = self._read()

        lines = [
            f"Nama: {data.get('name', '')}",
            f"Panggilan: {data.get('nickname', '')}",
            f"Bahasa: {data.get('language', '')}",
            f"Gaya respons: {data.get('response_style', '')}",
            f"Pekerjaan: {data.get('role', '')}",
        ]

        projects = data.get("projects", [])
        if projects:
            lines.append(f"Proyek: {', '.join(projects)}")

        contact = data.get("contact", {})
        if contact:
            lines.append(
                f"Kontak: {contact.get('email', '')} | "
                f"{contact.get('phone', '')} | "
                f"{contact.get('location', '')}"
            )

        skills = data.get("skills", [])
        if skills:
            lines.append(f"Skills: {', '.join(skills[:15])}")

        return "\n".join(lines)

    def raw(self) -> dict:
        """Full profile dict for auto-apply and structured access."""
        return self._read()

    def contact(self) -> dict:
        return self._read().get("contact", {})

    def experience_text(self) -> str:
        """Formatted experience for job applications."""
        data = self._read()
        lines = []
        for exp in data.get("experience", []):
            lines.append(f"{exp['title']} at {exp['company']} ({exp['period']})")
            for h in exp.get("highlights", []):
                lines.append(f"  - {h}")
            lines.append("")
        return "\n".join(lines)

    def summary_text(self) -> str:
        data = self._read()
        parts = [data.get("summary", "")]

        education = data.get("education", {})
        if education:
            parts.append(
                f"Education: {education.get('degree')} from {education.get('school')}"
                f" — GPA {education.get('gpa', '')}"
            )

        certs = data.get("certificates", [])
        if certs:
            parts.append(f"Certificates: {', '.join(certs)}")

        languages = data.get("languages_spoken", [])
        if languages:
            parts.append(f"Languages: {', '.join(languages)}")

        return "\n".join(parts)
