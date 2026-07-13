# LinkedIn Job Assist — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-07-13-linkedin-job-assist-design.md`
**Urutan:** backend dulu (bisa ditest tanpa extension), extension terakhir.
Tiap task punya verifikasi sendiri — selesai satu, jalan, baru lanjut.

## Task 1 — `_save_jobs` upgrade (job_hunt_tool.py)

Perubahan:
- Persist field `email` (default `""`).
- Dedup: kalau `source == "linkedin-ext"` → dedup by URL (lowercase, strip query), fallback
  title. Sumber lain tetep by title (behavior lama utuh).
- Return list id job yang beneran kesimpen (sekarang None).
- `ACTIONED` += `"drafted"`.

Verifikasi: extend `_demo()` — simpen 2 job URL sama → 1 kesimpen; 2 job judul sama URL beda
(linkedin-ext) → 2 kesimpen; return ids bener; job stage `drafted` gak kena cap/prune.
`uv run python -m app.tools.job_hunt_tool` hijau.

## Task 2 — Gmail OAuth reuse (scripts/google_auth.py)

Perubahan:
- Generalisir: arg ke-2 opsional `gmail` → scope `gmail.compose`, token ke
  `data/gmail_token_<label>.json`. Default tetep calendar (behavior lama utuh).
- Manual (Rendy, sekali): aktifin Gmail API di project Cloud existing, tambah scope
  `gmail.compose` di consent screen (mode Testing), lalu
  `uv run python scripts/google_auth.py pribadi gmail`.

Verifikasi: token file kebentuk, `creds.valid` true. (Manual step — gua pandu, gak bisa otomatis.)

## Task 3 — `app/tools/gmail_draft.py` (baru)

- `build_mime(to, subject, body, cv_path) -> MIMEMultipart` — attach PDF dari `CV_PATH` env.
- `create_draft(to, subject, body) -> draft_id` — service `gmail` v1 dari
  `data/gmail_token_pribadi.json` (pola `_build_service` calendar_tool), `users.drafts.create`.
- Subjek template: `"Application for {title} — {full_name}"` (full_name dari profile contact).

Verifikasi: `_demo()` — `build_mime` di-assert (to/subject/attachment ada), API call di-mock.
`uv run python -m app.tools.gmail_draft` hijau. Test live 1 draft beneran ke Gmail (manual cek).

## Task 4 — Endpoint ingest + drafts (dashboard.py)

- Dependency `require_ext_token`: header `X-API-Token` vs env `EXT_API_TOKEN`,
  `secrets.compare_digest`, salah → 401. Env kosong → endpoint mati (503).
- `POST /api/jobs/ingest`: batch array (schema spec), skor via `match_score` + `_llm_rescore`
  (fast_llm dari `create_core` — cek wiring, dashboard standalone; kalau ribet, heuristik doang
  dulu + catat), filter >= 75, `_save_jobs`, response per item.
- `POST /api/drafts {job_id}`: job harus punya `email`, stage belum `drafted` (kalau udah → 409).
  Panggil `gmail_draft.create_draft`, simpen `gmail_draft_id` ke job, set stage `drafted` di
  `STATUS_DB`. Gmail gagal → 502 + pesan jelas, job utuh.
- `GET /api/drafts`: list job stage `drafted` (+`gmail_draft_id`).
- `STAGES` += `"drafted"` (posisi setelah `saved`).

Verifikasi: `curl` tanpa token → 401; payload skor rendah → stored false; skor tinggi →
masuk `jobs.json`; drafts 2x → 409 kedua. TestClient/curl lokal.

## Task 5 — Dashboard UI kecil

- Kolom stage `drafted` muncul (STAGES udah nambah — cek render existing, kemungkinan otomatis).
- Tombol "Draft email" di job yang punya `email` → POST `/api/drafts`.

Verifikasi: buka dashboard lokal, klik, draft muncul di Gmail.

## Task 6 — Chrome extension (folder `extension/` di repo ini)

MV3, tanpa framework/build step — vanilla JS, load unpacked.
- `manifest.json`: content script @ `linkedin.com`, background service worker,
  `host_permissions` domain agent, `storage`.
- `options.html/js`: URL agent + API token → `chrome.storage`.
- `content.js`: dua mode by URL — feed search (post <= 1 jam: parse relative timestamp "1h/30m",
  ambil text post, regex email) dan jobs search (kartu job: title/company/location/URL; deskripsi
  ikut kalau kebuka). Tombol floating "Scan" — scan on-demand, bukan observer permanen (anti ban:
  gak ada aktivitas background terus-terusan).
- `background.js`: terima `chrome.runtime.sendMessage` dari content, POST batch ke
  `/api/jobs/ingest` header `X-API-Token`, badge hasil (n stored).

Verifikasi: load unpacked, search "nextjs job" di LinkedIn, klik Scan, cek `jobs.json` +
dashboard keisi, skor keliatan.

## Task 7 — E2E + docs

- Alur penuh: scan → ingest → skor → tombol draft → draft di Gmail (attach CV, tujuan bener) →
  send manual → stage `applied` via dashboard.
- Update `README.md` / `docs/dashboard.md`: env baru (`EXT_API_TOKEN`, `CV_PATH`), setup Gmail
  scope, setup extension.

## Risiko / catatan

- LinkedIn DOM berubah kapan aja → selector content.js bakal butuh maintenance; simpen selector
  di satu objek biar gampang patch.
- LLM rescore di dashboard container: dashboard sekarang standalone (gak import LLM). Kalau
  wiring fast_llm ribet, fase 1 pake heuristik `match_score` doang di ingest — threshold tetep
  jalan, LLM rescore nyusul. Keputusan pas Task 4.
- `EXT_API_TOKEN` + `CV_PATH` harus masuk env Coolify pas deploy.
