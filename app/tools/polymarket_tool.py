"""Pasar prediksi Polymarket + harga/odds terkini via Gamma API — no API key."""

import json
import time
import urllib.request


_cache = {}  # keyword -> (timestamp, result)

GAMMA = (
    "https://gamma-api.polymarket.com/markets"
    "?closed=false&active=true&order=volume24hr&ascending=false&limit=200"
)


class PolymarketTool:
    name = "polymarket"
    description = (
        "Lihat pasar prediksi Polymarket + harga/odds terkini. "
        "Input: kata kunci topik pasar (contoh 'election', 'bitcoin', 'premier league'), "
        "atau kosongin buat pasar paling rame hari ini. "
        "Balikin pertanyaan pasar, harga tiap outcome (0-100% = probabilitas pasar), "
        "volume 24 jam, tanggal tutup, dan perubahan harga 24 jam. "
        "Harga di sini = tebakan kolektif trader; bandingin sama analisa lo sendiri buat cari selisih."
    )

    def run(self, input: str = "") -> str:
        kw = input.strip().lower()
        ck = kw or "__top__"
        if ck in _cache:
            ts, res = _cache[ck]
            if time.time() - ts < 300:  # 5 menit
                return res

        try:
            req = urllib.request.Request(GAMMA, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read())

            # ponytail: filter kata kunci di sisi client atas 200 pasar ter-rame.
            # Cukup buat nemu pasar likuid yang relevan; kalau butuh full-text, pakai search endpoint.
            if kw:
                data = [m for m in data if kw in (m.get("question") or "").lower()]
            data = data[:6]

            if not data:
                return f"Gak nemu pasar Polymarket soal '{input.strip()}'."

            head = f"Pasar Polymarket soal '{input.strip()}':" if kw else "Pasar Polymarket paling rame:"
            return "\n\n".join([head] + [self._fmt(m) for m in data])
        except Exception as e:
            return f"Error ambil data Polymarket: {e}"

    def _fmt(self, m: dict) -> str:
        q = m.get("question", "?")
        try:
            outcomes = json.loads(m.get("outcomes") or "[]")
            prices = json.loads(m.get("outcomePrices") or "[]")
        except (ValueError, TypeError):
            outcomes, prices = [], []
        odds = ", ".join(f"{o} {float(p) * 100:.0f}%" for o, p in zip(outcomes, prices))

        vol = m.get("volume24hr") or m.get("volume") or 0
        try:
            vol = f"${float(vol):,.0f}"
        except (ValueError, TypeError):
            pass

        end = (m.get("endDate") or "")[:10]

        chg = m.get("oneDayPriceChange")
        chg_s = f" | 24j {chg * 100:+.1f}pt" if isinstance(chg, (int, float)) and chg else ""

        return f"• {q}\n  {odds or '(harga belum ada)'} | vol24j {vol} | tutup {end}{chg_s}"


def _selfcheck():
    # ponytail: _fmt parsing (JSON string outcomes/prices, zip, %) — satu-satunya logika non-trivial
    fake = {
        "question": "Will X happen?",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.23", "0.77"]',
        "volume24hr": 12345.6,
        "endDate": "2026-07-04T11:35:00Z",
        "oneDayPriceChange": 0.05,
    }
    out = PolymarketTool()._fmt(fake)
    assert "Yes 23%" in out and "No 77%" in out, out
    assert "$12,346" in out and "tutup 2026-07-04" in out, out
    assert "+5.0pt" in out, out
    # outcomes rusak jgn crash
    assert "(harga belum ada)" in PolymarketTool()._fmt({"question": "q", "outcomes": None}), "broken-case"
    print("polymarket _fmt selfcheck: OK")


if __name__ == "__main__":
    _selfcheck()
