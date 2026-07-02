from pathlib import Path

from app.tools.base import Tool

# ponytail: sandboxed to project_root/data/ — no path traversal
DATA_ROOT = Path("data")
DATA_ROOT.mkdir(exist_ok=True)


class FileTool(Tool):
    name = "files"
    description = (
        "File operations in data/ directory. Commands: read:<path>, "
        "write:<path>:<content>, list:<path>, delete:<path>"
    )

    def _resolve(self, rel: str) -> Path:
        path = (DATA_ROOT / rel).resolve()
        if not str(path).startswith(str(DATA_ROOT.resolve())):
            raise ValueError("path traversal not allowed")
        return path

    def run(self, input: str = "") -> str:
        parts = input.strip().split(":", 2)
        if len(parts) < 2:
            return "Error: format is cmd:path[:content]"

        cmd = parts[0].strip().lower()
        rel = parts[1].strip()
        extra = parts[2] if len(parts) > 2 else ""

        try:
            path = self._resolve(rel)

            if cmd == "read":
                if not path.exists():
                    return f"File not found: {rel}"
                text = path.read_text(encoding="utf-8")
                return text[:5000]

            if cmd == "write":
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(extra, encoding="utf-8")
                return f"Written: {rel} ({len(extra)} chars)"

            if cmd == "list":
                if not path.exists():
                    return f"Not found: {rel}"
                if path.is_file():
                    return f"{rel} ({path.stat().st_size} bytes)"
                entries = []
                for p in sorted(path.iterdir()):
                    suffix = "/" if p.is_dir() else ""
                    entries.append(f"  {p.name}{suffix}")
                return "\n".join(entries) if entries else "(empty)"

            if cmd == "delete":
                if not path.exists():
                    return f"Not found: {rel}"
                path.unlink()
                return f"Deleted: {rel}"

            return f"Error: unknown command '{cmd}'. Use read, write, list, delete."
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {e}"
