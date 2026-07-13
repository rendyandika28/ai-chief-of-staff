# LinkedIn Job Assist (Chrome Extension)

Tangan browser buat agent di repo ini: satu klik **Auto-scan** → scroll otomatis sampe feed
habis, kumpulin post lowongan yang nyantumin **email** (cold approach — tanpa email di-skip),
kirim ke agent buat scoring CV-match, dan yang lolos (≥75) langsung dibikinin **draft Gmail**
(cover letter + CV keattach). Semi-auto: Send tetep manual dari Gmail.

## Setup

1. `chrome://extensions` → Developer mode ON → **Load unpacked** → pilih folder ini.
2. Klik icon extension → Options → isi URL agent (dashboard), `EXT_API_TOKEN` (dari env
   server), dan umur post maksimal (default 24 jam).

## Pakai

1. Buka LinkedIn, search keyword (mis. "frontend"), filter **Posts**.
2. Klik **⌕ Auto-scan** (pojok kanan bawah). Extension scroll sendiri (max 25x, berhenti
   kalau feed habis), delay acak 1-2 detik antar scroll.
3. Tiap post dapet badge hasil di pojok kanan atasnya:
   - `✉ 92% — draft di Gmail` (hijau) — lolos skor, draft udah nunggu di Gmail
   - `⚠ 80% kesimpen, draft gagal` (kuning) — job kesimpen, draft error (cek server)
   - `⏭ tanpa email` / `⏭ 44% — skip` / `⏭ duplikat` / `⏭ > 24 jam` (abu) — di-skip
4. Selesai: buka **Gmail → Drafts**, review, Send. Tracking stage di dashboard.

## Catatan

- Selector DOM LinkedIn sering berubah — semua di objek `SEL` di `content.js`, patch di situ.
  LinkedIn 2026 pake "SDUI" (class di-hash): pegangan cuma `data-testid`/`componentkey`/`role`.
- Scan on-demand doang (gak ada observer background) — minim jejak automation.
- Jangan spam scan; LinkedIn ToS melarang scraping. Risiko akun tanggung sendiri.
