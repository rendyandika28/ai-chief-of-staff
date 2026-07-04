"""Berita terkini per topik via Google News RSS (Indonesia) — no API key."""

import time
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape


_cache = {}  # query -> (timestamp, result)


class NewsTool:
    name = "news"
    description = (
        "Berita terkini soal sebuah topik dari Google News (Indonesia). "
        "Input: kata kunci topik, contoh 'pemilu AS', 'bitcoin', 'gencatan senjata'. "
        "Balikin daftar judul + sumber + waktu terbit + link. "
        "Pakai buat cari tau situasi/kabar terbaru sebelum ngasih analisa."
    )

    def run(self, input: str = "") -> str:
        q = input.strip()
        if not q:
            return "Error: kasih topik/kata kunci beritanya"

        key = q.lower()
        if key in _cache:
            ts, res = _cache[key]
            if time.time() - ts < 600:  # 10 menit
                return res

        try:
            url = (
                "https://news.google.com/rss/search?q="
                + urllib.request.quote(q)
                + "&hl=id&gl=ID&ceid=ID:id"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                root = ET.fromstring(r.read())

            items = root.findall(".//item")[:8]
            if not items:
                return f"Gak nemu berita soal '{q}'."

            lines = [f"Berita terbaru soal '{q}':"]
            for it in items:
                title = unescape(it.findtext("title") or "").strip()  # udah termasuk "- Sumber"
                pub = (it.findtext("pubDate") or "").strip()
                link = (it.findtext("link") or "").strip()
                lines.append(f"- {title} ({pub})\n  {link}")

            res = "\n".join(lines)
            _cache[key] = (time.time(), res)
            return res
        except Exception as e:
            return f"Error ambil berita: {e}"
