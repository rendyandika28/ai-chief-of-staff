import glob
import os
import time as _time
from datetime import datetime
from app.tools.base import Tool


class PlaywrightSession:
    """Encapsulates Playwright browser lifecycle. Duck-typed interface:
    navigate, click, type_text, content, screenshot, eval."""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None

    def _ensure_page(self):
        if self._playwright is None:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
        if self._browser is None:
            self._browser = self._playwright.chromium.launch(headless=True, args=[
                "--autoplay-policy=no-user-gesture-required",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ])
        if self._page is None or self._page.is_closed():
            self._page = self._browser.new_page()
        return self._page

    def navigate(self, url: str) -> str:
        page = self._ensure_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return f"Title: {page.title()}\nURL: {page.url}"

    def click(self, selector: str) -> str:
        page = self._ensure_page()
        page.click(selector, timeout=10000)
        return f"Clicked {selector}"

    def type_text(self, selector: str, text: str) -> str:
        page = self._ensure_page()
        page.fill(selector, text)
        return f"Typed into {selector}"

    def content(self) -> str:
        page = self._ensure_page()
        return page.inner_text("body")[:3000]

    def screenshot(self) -> str:
        page = self._ensure_page()
        path = f"memory/screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        page.screenshot(path=path, full_page=True)
        return f"Screenshot saved: {path}"

    def eval(self, js: str) -> str:
        page = self._ensure_page()
        return str(page.evaluate(js))

    def close(self):
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
        self._page = None

    def record_video(self, html_content: str, duration: int, output_path: str) -> str:
        """Create a new context, load HTML, record video for `duration` seconds,
        close context, return output_path if successful or empty string."""
        self._ensure_page()
        if self._browser is None:
            return ""

        video_dir = os.path.abspath(os.path.dirname(output_path))
        abs_output = os.path.abspath(output_path)
        os.makedirs(video_dir, exist_ok=True)

        ctx = self._browser.new_context(
            record_video_dir=video_dir,
            record_video_size={"width": 1280, "height": 720},
            viewport={"width": 1280, "height": 720},
        )
        page = ctx.new_page()
        page.set_content(html_content, timeout=15000)
        _time.sleep(duration)
        ctx.close()

        videos = sorted(glob.glob(os.path.join(video_dir, "*.webm")))
        if videos:
            os.rename(videos[-1], abs_output)
            return abs_output
        return ""


class BrowserTool(Tool):
    name = "browser"
    description = (
        "Browser automation. Commands: navigate:<url>, click:<selector>, "
        "type:<selector>:<text>, content, screenshot, eval:<js>. "
        "Multiple commands separated by newline run in sequence."
    )

    def __init__(self, session=None):
        self._session = session if session is not None else PlaywrightSession()

    def run(self, input: str = "") -> str:
        lines = input.strip().split("\n")
        results = []

        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = line.split(":", 1)
            cmd = parts[0].strip()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd == "navigate":
                results.append(self._session.navigate(arg))
            elif cmd == "click":
                results.append(self._session.click(arg))
            elif cmd == "type":
                sel, _, text = arg.partition(":")
                results.append(self._session.type_text(sel.strip(), text.strip()))
            elif cmd == "content":
                results.append(self._session.content())
            elif cmd == "screenshot":
                results.append(self._session.screenshot())
            elif cmd == "eval":
                results.append(self._session.eval(arg))
            else:
                results.append(f"Unknown command: {cmd}")

        return "\n".join(results)
