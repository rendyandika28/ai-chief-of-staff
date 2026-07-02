import urllib.request
import urllib.error
import re

from app.tools.base import Tool


class WebFetchTool(Tool):
    name = "webfetch"
    description = "Fetch content from a URL and return the page title + text summary"

    def run(self, input: str = ""):
        url = input.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AI-Chief-of-Staff/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if "text/html" not in content_type and "text/plain" not in content_type:
                    return f"Not an HTML/text page (Content-Type: {content_type})"

                body = resp.read().decode("utf-8", errors="replace")

            title = "No title"
            match = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
            if match:
                title = match.group(1).strip()

            text = re.sub(r"<script[^>]*>.*?</script>", "", body, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()

            if len(text) > 2000:
                text = text[:2000] + "..."

            return f"Title: {title}\n\n{text}"

        except urllib.error.URLError as e:
            return f"Failed to fetch URL: {e}"
        except Exception as e:
            return f"Error: {e}"
