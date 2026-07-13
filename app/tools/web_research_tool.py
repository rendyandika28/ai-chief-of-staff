"""Web research — DuckDuckGo search + light page fetch (raw, truncated).

Returns search results plus the trimmed text of the top pages. The tool does NOT
call an LLM — the agent synthesizes the answer in-persona from what's returned.
ponytail: raw truncated text; add Haiku page-compression if main context bloats.
ponytail: ddgs (no key); swap to Brave Search API if DDG rate-limits get rough.
"""

import re
import urllib.request

MAX_RESULTS = 5
FETCH_URLS = 2          # only read the top N pages
PAGE_CAP = 6000         # chars kept per fetched page
BYTE_CAP = 400_000      # bytes read before we stop (guard against huge pages)
TIMEOUT = 8             # seconds per fetch
_UA = "Mozilla/5.0 (compatible; ChiefBot/1.0)"


def _strip_html(html: str) -> str:
    """Crude HTML → text: drop script/style, strip tags, collapse whitespace."""
    html = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _search(query: str) -> list:
    """DDG text search → [{title, href, body}]. Raises on failure (caller guards)."""
    from ddgs import DDGS
    with DDGS() as d:
        return list(d.text(query, max_results=MAX_RESULTS))


def _fetch(url: str) -> str:
    """Fetch a page, return trimmed readable text, or '' on any problem."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "html" not in ctype and "text" not in ctype:
                return ""  # skip PDFs/binaries
            raw = resp.read(BYTE_CAP)
        return _strip_html(raw.decode("utf-8", "ignore"))[:PAGE_CAP]
    except Exception:
        return ""


class WebResearchTool:
    name = "web_research"
    description = (
        "Riset web beneran: cari + baca halaman buat info terkini/faktual "
        "(berita, perusahaan, harga, gaji, orang, topik apapun yang butuh sumber "
        "terbaru). Beda dari 'news' yang cuma feed. "
        "Input: query/pertanyaan. Contoh: 'gaji frontend engineer jakarta 2026'"
    )

    def run(self, input: str = "", user_id: str = "") -> str:
        query = (input or "").strip()
        if not query:
            return "Error: query kosong"
        try:
            results = _search(query)
        except Exception as e:
            return f"(search gagal: {e})"
        if not results:
            return f"(gak ada hasil buat '{query}')"

        lines = [f"Hasil cari '{query}':"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', '')} — {r.get('href', '')}\n"
                         f"   {r.get('body', '')}")

        for r in results[:FETCH_URLS]:
            url = r.get("href", "")
            text = _fetch(url) if url else ""
            if text:
                lines.append(f"\n--- isi {url} ---\n{text}")

        return "\n".join(lines)


def _demo():
    """Self-check: python -m app.tools.web_research_tool — no network.

    Rebind the module globals run() actually resolves. (import-as-m fails here:
    `python -m` loads this file twice — as __main__ and as the package module —
    so patching the other copy misses the one run() uses.)"""
    global _search, _fetch

    # _strip_html: script/style dropped, tags gone, whitespace collapsed
    html = "<html><style>x{}</style><body>Halo  <b>dunia</b>\n<script>evil()</script>ok</body></html>"
    assert _strip_html(html) == "Halo dunia ok", _strip_html(html)

    # run(): assemble results + fetched page, offline via stubbed globals
    _search = lambda q: [
        {"title": "Gaji FE", "href": "http://a.com", "body": "kisaran 15-30jt"},
        {"title": "Panduan", "href": "http://b.com", "body": "tergantung level"},
    ]
    _fetch = lambda url: "isi lengkap halaman" if url == "http://a.com" else ""
    out = WebResearchTool().run("gaji frontend jakarta")
    assert "Gaji FE" in out and "http://a.com" in out, out
    assert "isi lengkap halaman" in out, "top page text should be included"
    assert "kisaran 15-30jt" in out, "snippet should be included"

    # empty query + no-results guards
    assert "kosong" in WebResearchTool().run("")
    _search = lambda q: []
    assert "gak ada hasil" in WebResearchTool().run("zzz")

    # search failure → graceful string, no raise
    def _boom(q):
        raise RuntimeError("ratelimited")
    _search = _boom
    assert "search gagal" in WebResearchTool().run("apa aja")

    print("web_research self-check OK")


if __name__ == "__main__":
    _demo()
