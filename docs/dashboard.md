# Ops Console — dashboard monitoring

Dashboard read-only buat mantau agent: aktivitas langsung, status/health, memory & knowledge, riwayat chat. Baca file SQLite yang sama yang ditulis bot (read-only). Bot tetap jalan sebagai systemd; dashboard jalan sebagai container Coolify terpisah.

## Cara jalan (lokal / dev)

```bash
DASH_PASS=rahasia uv run uvicorn app.interfaces.dashboard:app --port 8000
# buka http://localhost:8000  (user default: admin, password: rahasia)
```

Env:
- `DASH_USER` — username login (default `admin`)
- `DASH_PASS` — **wajib**. Tanpa ini semua request 500.
- `MEMORY_DIR` — folder berisi `*.db` bot (default `memory`; di container `/memory`)
- `EXT_API_TOKEN` — token buat Chrome extension (LinkedIn Job Assist). Kosong = endpoint
  extension mati (503). Generate: `openssl rand -hex 24`.
- `GROQ_API_KEY` — opsional; kalau ada, ingest extension di-rescore LLM (tanpa = heuristik doang).

## Deploy di Coolify

1. **New Resource → Application → dari repo ini**, build pack **Dockerfile**.
2. **Port**: `8000`. Set **Domain** → Coolify urus HTTPS otomatis.
3. **Environment variables**:
   - `DASH_USER` = pilih username
   - `DASH_PASS` = password kuat (ini satu-satunya pengaman; URL-nya publik)
4. **Storage → tambah bind mount** (biar container liat data bot):
   - Host: `/root/ai-chief-of-staff/memory`  →  Container: `/memory`  →  **read only: on**
5. Deploy. Buka domainnya, login pakai kredensial di atas.

> ⚠️ Dashboard nampilin semua chat & memory pribadi. Jangan share URL/password. Ganti `DASH_PASS` kalau bocor.

## LinkedIn Job Assist (extension → dashboard)

Chrome extension (repo `linkedin-job-assist-ext`) scan post/job LinkedIn dan push ke sini.
Spec: `docs/superpowers/specs/2026-07-13-linkedin-job-assist-design.md`.

- `POST /api/jobs/ingest` — batch dari extension. Auth: header `X-API-Token` == `EXT_API_TOKEN`
  (atau basic auth dashboard). Skor lawan CV, cuma ≥ 75 kesimpen.
- `POST /api/drafts {job_id}` — bikin Gmail draft (cover letter + CV attach) buat job ber-email.
  Stage job → `drafted`. Dobel → 409.
- `GET /api/drafts` — list job stage `drafted`.

Prasyarat Gmail draft (sekali):
1. Google Cloud project existing (yang calendar): enable **Gmail API** + scope
   `gmail.compose` di consent screen.
2. `uv run python scripts/google_auth.py pribadi gmail` → `data/gmail_token_pribadi.json`.
3. Taro CV di `data/resume.pdf` (path dari `resume_path` di `memory/profile.json`).

## Catatan
- Status **AWAKE** dihitung dari heartbeat scheduler (tiap 30 dtk). Kalau bot mati > 90 dtk → status jadi "TIDAK AKTIF".
- Dashboard nulis 3 file: `job_status.json` (stage), `jobs.json` (via ingest/draft) — mount
  `/memory` butuh **write** buat fitur job; sisanya read-only murni.
