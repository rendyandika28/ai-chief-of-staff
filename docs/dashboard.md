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

## Catatan
- Status **AWAKE** dihitung dari heartbeat scheduler (tiap 30 dtk). Kalau bot mati > 90 dtk → status jadi "TIDAK AKTIF".
- Read-only murni: dashboard gak pernah nulis ke DB bot.
