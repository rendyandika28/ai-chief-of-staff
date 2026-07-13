"""Browser tool — render a URL with real Chromium and return the visible text.

Complements web_research: web_research does search + quick static fetch; browse
handles pages that need JS rendering or that block plain HTTP (bot detection).
The agent feeds it a URL (usually one web_research surfaced) when a normal fetch
came back thin or empty.

Playwright's sync API can't run inside an asyncio loop, and the bot iterates the
agent's sync generator on the event-loop thread — so we run Playwright in a
worker thread (a clean thread has no running loop). The loop is already blocked
during a turn, so this adds a few seconds in the same blocking style.
"""

import re
import threading
from urllib.parse import urlparse

NAV_TIMEOUT = 15_000   # ms for page.goto
JOIN_TIMEOUT = 30      # s to guard a hung browser thread
TEXT_CAP = 8000        # chars returned


def _render(url: str) -> dict:
    """Run Playwright in a fresh thread. Returns {"text": ...} or {"err": ...}."""
    result = {}

    def work():
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
                    result["text"] = page.inner_text("body")
                finally:
                    browser.close()
        except Exception as e:
            result["err"] = str(e).splitlines()[0][:200]

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout=JOIN_TIMEOUT)
    if t.is_alive():
        return {"err": "timeout — halaman kelamaan render"}
    return result


class BrowserTool:
    name = "browse"
    description = (
        "Buka SATU halaman web pakai browser beneran (Chromium) buat baca isinya — "
        "khusus halaman JS-heavy / yang keblok fetch biasa (web_research balik kosong/tipis). "
        "Input: URL lengkap (http/https). Contoh: https://example.com/artikel. "
        "Pake web_research dulu buat nyari URL-nya."
    )

    def run(self, input: str = "", user_id: str = "") -> str:
        url = (input or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return "Error: kasih URL lengkap (http/https)"
        # ponytail: no private-IP/SSRF block — single-user, agent-driven from
        # public web_research URLs; add a block if the tool ever takes raw input.
        out = _render(url)
        if "text" not in out:
            return f"(gagal buka {url}: {out.get('err', 'unknown')})"
        text = re.sub(r"\s+\n", "\n", out["text"]).strip()
        text = re.sub(r"[ \t]{2,}", " ", text)
        if not text:
            return f"(halaman {url} kosong / ga ada teks)"
        return f"Isi {url}:\n{text[:TEXT_CAP]}"


def _demo():
    """Self-check: python -m app.tools.browser_tool — no network/browser.

    Rebind the module global run() resolves (import-as-m fails under `python -m`,
    which loads this file twice — see web_research_tool._demo)."""
    global _render

    # scheme validation — no render attempted
    assert "URL lengkap" in BrowserTool().run("not a url")
    assert "URL lengkap" in BrowserTool().run("ftp://x.com")
    assert "URL lengkap" in BrowserTool().run("")

    # success path via stubbed _render
    _render = lambda url: {"text": "Judul\n\n  Isi   artikel   ter-render."}
    out = BrowserTool().run("https://example.com/a")
    assert "example.com" in out and "Isi artikel ter-render." in out, out

    # render failure → graceful string, no raise
    _render = lambda url: {"err": "net::ERR_NAME_NOT_RESOLVED"}
    assert "gagal buka" in BrowserTool().run("https://nope.invalid")

    # empty page
    _render = lambda url: {"text": "   "}
    assert "kosong" in BrowserTool().run("https://blank.com")

    print("browser self-check OK")


if __name__ == "__main__":
    _demo()
