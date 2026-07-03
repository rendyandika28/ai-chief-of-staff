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
