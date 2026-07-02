import json
import urllib.request
import urllib.error

from app.tools.base import Tool


class HttpTool(Tool):
    name = "http"
    description = (
        "Make HTTP requests. Commands: get:<url>, post:<url>:<json_body>. "
        "Returns response body (truncated at 3000 chars)."
    )

    def run(self, input: str = "") -> str:
        parts = input.strip().split(":", 2)
        if len(parts) < 2:
            return "Error: format is get:<url> or post:<url>:<json_body>"

        method = parts[0].strip().lower()
        url = parts[1].strip()
        body = parts[2].strip() if len(parts) > 2 else None

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            if method == "get":
                return self._request(url)
            elif method == "post":
                return self._request(url, method="POST", body=body)
            else:
                return f"Error: unknown method '{method}'. Use get or post."
        except urllib.error.HTTPError as e:
            return f"HTTP {e.code}: {e.reason}"
        except Exception as e:
            return f"Error: {e}"

    def _request(self, url: str, method: str = "GET", body: str = None) -> str:
        data = None
        if body:
            data = body.encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": "AI-Chief-of-Staff/1.0",
                "Content-Type": "application/json",
            },
            method=method,
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        if len(raw) > 3000:
            raw = raw[:3000] + "..."

        try:
            parsed = json.loads(raw)
            return json.dumps(parsed, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, ValueError):
            return raw
