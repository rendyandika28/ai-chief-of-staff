# LinkedIn Job Assist — Design

**Date:** 2026-07-13
**Status:** Approved, pre-implementation
**Scope:** Phase 1 — LinkedIn only. Portal lain (Glints/Indeed/WWR) nyusul pola sama.

## Masalah

`job_hunt_tool.py` sekarang tarik job dari API gratis server-side, skor lawan CV, generate
cover letter, track status di dashboard. Tapi gak nyentuh yang Rendy mau: baca **post LinkedIn**
(bukan cuma job listing), ekstrak email dari post, dan siapin **email lamaran** buat dikirim.
Post LinkedIn + auto-apply cuma bisa dari browser yang login sebagai Rendy — server Python gak
bisa pegang sesi authenticated LinkedIn.

## Prinsip

- **Extension = tangan** (browser, sesi login Rendy). Scrape + ekstrak doang, tipis.
- **Agent = otak** (skor, draft, cover letter, report). Semua logika di sini.
- **Dashboard = mata** (track job + draft).
- **Semi-auto, bukan fully-auto.** Agent bikin *draft* Gmail; Rendy cek + Send manual. Anti-spam,
  jaga reputasi, hindari risiko ban LinkedIn dari kirim massal.
- **Gak asal apply.** Cuma job/post dengan skor >= 75 (threshold existing) yang didraft.
- **Reuse maksimal.** `match_score`, `_llm_rescore`, `build_cover_letter`, retensi/prune,
  stage tracking, FastAPI dashboard, OAuth Google — semua udah ada, dipake ulang.

## Arsitektur

```
┌─ Chrome Extension (repo baru) ──────────┐
│  content script @ linkedin.com          │
│  - baca post <=1 jam (by keyword)       │
│  - baca job listing (by keyword)        │
│  - ekstrak email dari body post         │
│  - POST ke agent, header X-API-Token    │
└──────────────┬──────────────────────────┘
               │ POST /api/jobs/ingest
               ▼
┌─ FastAPI (app/interfaces/dashboard.py) ─┐
│  1. verifikasi X-API-Token              │
│  2. skor (match_score + _llm_rescore)   │
│  3. skor < 75 → buang, stop             │
│  4. skor >= 75 → simpen + trigger draft │
└──────────────┬──────────────────────────┘
               ▼
      SQLite + jobs.json (store SAMA dgn agent)
               ▲
        Agent baca: report "lagi apply apa", followup, remind
```

Extension gak pernah sentuh SQLite langsung (file di server, gak bisa diakses browser).
Semua lewat HTTP ke FastAPI yang udah jalan. Satu DB, dua penulis (dashboard + extension), via HTTP.

## Komponen

### Baru

| Komponen | Lokasi | Tugas |
|---|---|---|
| Chrome extension | repo/folder baru (di luar repo agent) | scrape post+job LinkedIn, ekstrak email, push ke agent |
| `POST /api/jobs/ingest` | `app/interfaces/dashboard.py` | terima payload extension, skor, filter >=75, simpen |
| `POST /api/drafts` | `app/interfaces/dashboard.py` | bikin Gmail draft buat job yang lolos |
| `GET /api/drafts` | `app/interfaces/dashboard.py` | list draft buat dashboard |
| Token auth | `app/interfaces/dashboard.py` | cek `X-API-Token` == env `EXT_API_TOKEN` |
| `app/tools/gmail_draft.py` | tool baru | Gmail API: bikin draft (cover letter + attach CV) |

### Reuse

- `match_score`, `_llm_rescore`, `build_cover_letter` — dipake apa adanya.
- `JOB_DB` (`jobs.json`), `STATUS_DB` (`job_status.json`) — store existing.
- FastAPI app + HTTP Basic dashboard existing.
- OAuth Google existing: project Cloud + `data/credentials.json` sama.

### Perubahan kecil di kode existing (wajib, bukan opsional)

- `job_hunt_tool.py` `_save_jobs`: (a) persist field `email` (sekarang whitelist field bakal
  buang diem-diem), (b) return list `id` job yang beneran kesimpen (sekarang return None,
  ingest butuh `job_id` buat response), (c) buat job `source: "linkedin-ext"` dedup by **URL**
  (LinkedIn URL unik per post), fallback title — dedup by-title doang bakal buang post valid
  dari dua perusahaan beda yang judulnya sama ("Frontend Developer (Remote)").
- `job_hunt_tool.py` `ACTIONED`: tambah `"drafted"`. Tanpa ini job yang udah didraft tetep
  keitung kuota `SAVED_CAP` dan bisa kepruned 30 hari → draft ada di Gmail, job ilang dari tracking.
- `dashboard.py` `STAGES`: tambah `"drafted"` (urutan: saved → drafted → applied → ...).

## Payload ingest

Extension POST `/api/jobs/ingest` — **array** (batch), bukan per-item. Extension baca banyak
post sekaligus; batch = 1 request + `_llm_rescore` udah batch 8 per LLM call, gak burst.

```json
{
  "items": [
    {
      "type": "post" | "job",
      "title": "Hiring: Senior Next.js Engineer",
      "body": "full text post / deskripsi job",
      "email": "hr@foo.com" | null,
      "company": "Foo",
      "location": "Remote",
      "url": "https://linkedin.com/..."
    }
  ]
}
```

Agent sisi ingest:
1. Skor semua item: `match_score` (feed `body` sebagai `description`) + `_llm_rescore` (batch).
2. Item `< 75` → dibuang. **Anti asal-apply.**
3. Item `>= 75` → simpen ke `jobs.json` (`_save_jobs`), `source: "linkedin-ext"`, `email` ikut.
4. Response per item: `[{"url": ..., "stored": true|false, "score": N, "job_id": N|null}]`.

## Alur draft (satu post lolos → email siap)

1. Rendy search "nextjs job" di LinkedIn, aktifin extension.
2. Extension temu post <=1 jam, ada email `hr@foo.com`, POST ke ingest.
3. Agent skor >= 75 → simpen job (punya `email`).
4. Extension (atau dashboard) POST `/api/drafts` `{job_id}`. Job yang stage-nya udah
   `drafted` → 409, gak bikin draft dobel di Gmail.
5. Agent: body = `build_cover_letter(job, profile)` (existing, body doang — gak bikin subjek).
   Subjek = template terpisah: `"Application for {title} — {full_name}"`. `gmail_draft` bikin
   draft via Gmail API, attach CV (path PDF dari env `CV_PATH`), tujuan = `job.email`.
6. Draft mendarat di **folder Draft Gmail pribadi Rendy**. Stage job → `drafted`.
7. Rendy buka Gmail, cek, **Send manual**.
8. Agent tau Rendy lagi apply ke Foo (dari `jobs.json`); followup email = fase 2.

Job tanpa `email` (job listing biasa, bukan post): gak bisa didraft — tetep disimpen buat
apply manual lewat URL, sama kayak sekarang.

## Gmail draft — reuse OAuth existing

Reuse project Google Cloud + `data/credentials.json` yang sama (dipake calendar). Yang perlu:

1. Aktifin **Gmail API** di project Cloud itu.
2. Tambah scope `https://www.googleapis.com/auth/gmail.compose` di OAuth consent screen (mode
   Testing cukup — cuma email pribadi Rendy, gak perlu verifikasi Google).
3. Generalisir `scripts/google_auth.py` biar bisa minta scope Gmail, simpen token ke file
   **terpisah** `data/gmail_token_pribadi.json`. JANGAN gabung ke token calendar — gabung scope
   = token calendar minta consent ulang. Pisah = calendar aman.
4. Rendy consent sekali (email pribadi). Refresh token kesimpen. Draft berikutnya tanpa login ulang.

`gmail_draft.py`: bangun service `gmail`, buat MIME message (cover letter + attach CV), panggil
`users.drafts.create`. Scope `gmail.compose` = cuma bisa bikin draft, gak baca inbox. Deteksi
followup (butuh `gmail.readonly`) = fase 2, di luar scope ini.

## Auth extension

- Env baru `EXT_API_TOKEN` (random string, di-generate sekali).
- Extension simpen token di `chrome.storage`, kirim header `X-API-Token` tiap request.
- FastAPI reject request ke `/api/jobs/ingest` + `/api/drafts` kalau token gak cocok (401).
  Compare pake `secrets.compare_digest` (timing-safe, konsisten sama basic auth dashboard).
- Bisa dicabut/ganti tanpa ganggu login dashboard (HTTP Basic terpisah).

## Catatan extension (blocker kalau kelupaan)

- **CORS**: content script gak boleh fetch langsung ke domain agent (cross-origin dari
  linkedin.com = keblok). Fetch dilakukan **background service worker**, content script kirim
  data via `chrome.runtime.sendMessage`. Manifest butuh `host_permissions` buat domain agent.
- Manifest V3. Konfigurasi extension (URL agent + token) lewat options page, kesimpen di
  `chrome.storage`.

## Data / retensi

- Job dari extension masuk `jobs.json` lewat `_save_jobs` → dapet retensi/prune/cap gratis.
- `source: "linkedin-ext"` bedain dari hasil API server.
- Field tambahan: `email` (buat draft), `gmail_draft_id` (id draft dari Gmail API, buat trace).
- Stage baru `drafted` — masuk `ACTIONED` (lihat "Perubahan kecil" di atas) biar gak kepruned.

## Error handling

- Skoring gagal (LLM down) → fallback skor heuristik (udah begini di `_fetch_jobs`).
- Gmail API gagal (token expired non-refreshable, kuota) → return error jelas ke dashboard,
  job tetep tersimpen (Rendy bisa apply manual). Gak ada data ilang.
- Token extension salah → 401, extension tampilin "token invalid".
- Ingest dedup: by URL buat sumber extension (lihat "Perubahan kecil"), gak dobel.
- Draft dedup: stage udah `drafted` → `/api/drafts` balas 409.

## Testing

- `gmail_draft.py`: self-check `_demo()`/`__main__` — bangun MIME message, assert punya
  attachment + tujuan + subjek. Panggilan Gmail API di-mock (jangan hit network).
- Ingest endpoint: skor < 75 → `stored: false`; >= 75 → tersimpen. Test dengan payload fake +
  profile fake (pola `_FakeLLM` existing di `job_hunt_tool._demo`).
- Token auth: request tanpa/ salah token → 401.

## Di luar scope (fase berikutnya)

- Portal selain LinkedIn (Glints/Indeed/WWR extension).
- Deteksi email followup otomatis (butuh `gmail.readonly`).
- Fully-auto send (sengaja dihindari — semi-auto lebih aman).
- Auto-fill form apply di web (cuma email draft dulu).
