# Open-Loop Tracker — Design

**Tanggal:** 2026-07-10
**Status:** Approved (v1 chat-only)

## Masalah

Arra udah punya infra proaktif (morning brief, alert kalender, nudge topik basi, job scraper) tapi tetep kerasa reaktif: dia nyapa + jawab, gak *ngurusin* hal yang Rendy sebut sambil lalu. Komitmen kerjaan, deadline, keputusan, hal personal — kesebut di chat terus ilang. Rendy mau Arra jadi asisten beneran: nangkep hal-hal itu dan munculin balik di waktu yang pas.

## Ide inti

Satu primitif: **open-loop tracker**. Dari obrolan biasa, Arra diem-diem nangkep hal yang butuh ditindak, simpen, munculin pas relevan. Satu mekanisme ngelayanin kerjaan + second-brain + hidup sekaligus.

**Keputusan desain (dari brainstorm):**
- Input: **passive capture** dari chat. Rendy gak perlu command khusus.
- Perilaku: **quiet**. Simpen tanpa ganggu; munculin di morning brief + ping sekali pas mepet deadline.
- Scope v1: **chat-only**. Gak ada dashboard view (nyusul kalau kerasa butuh).

## Arsitektur

Reuse penuh infra yang ada. Komponen baru minimal.

### Komponen

**`app/agent/open_loops.py`** (baru) — `OpenLoops` store, pola persis `LongTermMemory`.
- Konstruktor: `OpenLoops(llm, db_path="memory/open_loops.db")` — nyimpen `fast_llm` (Haiku) buat ekstraksi.
- Tabel SQLite (reuse `Database`):
  ```sql
  CREATE TABLE IF NOT EXISTS loops (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id TEXT NOT NULL,
      text TEXT NOT NULL,
      due_at TEXT,              -- ISO date/datetime WIB, atau NULL kalau gak ada waktu
      kind TEXT DEFAULT 'work', -- work | personal | decision
      status TEXT DEFAULT 'open', -- open | done | expired
      created_at TEXT NOT NULL,
      surfaced_at TEXT          -- kapan terakhir di-ping (biar gak spam)
  )
  ```
- Method:
  - `ingest(user_id, message)` — pre-filter regex → kalau lolos, 1 call Haiku ekstrak loop → insert yang `status='open'`. Best-effort: exception ditelen (jangan sampe ganggu chat).
  - `due_soon(user_id, within_hours=18)` — loop `open` yang `due_at` dalam window & `surfaced_at` NULL (buat ping). Stamp `surfaced_at` pas dipanggil buat ping.
  - `agenda(user_id)` — loop overdue + jatuh tempo hari ini/besok (buat morning brief; gak nge-stamp).
  - `mark_done(user_id, hint)` — cari loop open paling relevan sama `hint` (LIKE/keyword), set `done`. Dipake pas Rendy bilang "udah".
  - `expire_stale(user_id)` — loop open yang lewat `due_at` > 3 hari → `expired`.

### Ekstraksi (silent capture)

Pre-filter regex sebelum manggil Haiku (hemat call): pesan mengandung kata waktu/komitmen —
`harus|kudu|jangan lupa|deadline|besok|lusa|nanti|Senin..Minggu|Jan..Des|tanggal|jam \d|minggu depan|bulan depan|inget(in)?`.
Gak lolos → skip, gak ada call.

Lolos → Haiku dengan system prompt:
- Konteks: hari ini `{tanggal WIB}`, timezone WIB.
- Tugas: dari 1 pesan user, keluarin JSON array open-loop. Tiap item `{text, due_at, kind}`.
- **Anti false-positive (kritis):** cuma ambil kalau ini komitmen/tugas nyata yang butuh tindakan Rendy nanti. Obrolan biasa, opini, pertanyaan, fakta lampau → array kosong `[]`.
- `due_at`: resolve relatif ("Jumat" → tanggal Jumat terdekat) ke ISO. Gak ada waktu jelas → `null`.
- Parse pake `extract_json` (helper yang udah ada di `app/schema.py`).

### Wiring

**`app/agent/agent.py`**
- Konstruktor terima `open_loops=None`, simpen `self.open_loops`.
- Di `_process`, abis loop streaming (deket `self.long_term.add(...)`): kalau `self.open_loops`, panggil `self.open_loops.ingest(user_id, message)`. Best-effort, gak nge-block reply.

**`app/app.py`** (`create_core`)
- Bikin `open_loops = OpenLoops(fast_llm)`; oper ke `Agent(...)`.
- Watcher baru `deadline_ping` (interval 3600s):
  ```
  now WIB; kalau di luar jam 9–21 → None
  loops = open_loops.due_soon(USER_ID)   # nge-stamp surfaced_at
  kalau kosong → None
  return agent.phrase("Sentil Rendy soal deadline mepet ini, santai, ringkas: <loops>")
  ```
- `morning_brief`: tambah seksi dari `open_loops.agenda(USER_ID)` sebelum manggil `agent.phrase`.
- `expire_stale`: panggil di dalam `deadline_ping` (sekalian, sebelum `due_soon`) — gak perlu watcher terpisah.

**`prompts/system.md`**
- Seksi baru "Open Loop": jelasin Arra diem-diem nyimpen komitmen Rendy, dan pas Rendy bilang suatu hal udah kelar/beres → panggil tool `loop_done` sama hint teksnya.

### Clear via chat

Rendy bales "udah/kelar/beres" pas disentil → loop ke-`done`.
- Tool tipis **`loop_done`** (`app/tools/`, reuse pola tool + `factory.py`): input = hint teks → manggil `open_loops.mark_done`. Persona nyuruh Arra panggil pas Rendy konfirmasi kelar.
- Auto-expire nangkep sisa yang gak pernah di-clear manual (lewat due + 3 hari).
- Salah tangkap ("bukan/hapus itu"): v1 cukup diemin — auto-expire yang bersihin. Hapus manual nyusul kalau kerasa perlu.

## Data & privasi

- `memory/open_loops.db` — runtime data, masuk `.gitignore` (kayak db lain).
- Single-user (Rendy). Gak ada data sensitif keluar selain ke Haiku pas ekstraksi (pesan chat, yang udah lewat LLM juga buat reply).

## Error handling

- `ingest` best-effort: semua exception ditelen + di-`log_event("error", ...)`. Chat gak boleh ke-block/gagal gara-gara ekstraksi.
- Haiku balikin JSON rusak → `extract_json` balikin kosong → 0 loop, aman.
- `deadline_ping`/`morning_brief`: udah dibungkus pola watcher yang toleran error.

## Testing

`open_loops.py` bawa `__main__` self-check (`python -m app.agent.open_loops`), nol network:
- `_FakeLLM` balikin JSON tetap.
- `ingest`: pesan komitmen ("kirim proposal Jumat") → 1 loop; obrolan biasa ("lagi mager") → pre-filter/Haiku → 0 loop.
- `due_soon`: loop due dalam 18 jam & `surfaced_at` NULL → kepilih + ke-stamp; dipanggil ke-2 → gak kepilih lagi.
- `agenda`: overdue + due hari ini kepilih, due minggu depan nggak.
- `expire_stale`: loop lewat due 4 hari → expired; lewat 1 hari → tetep open.
- `mark_done`: hint cocok → status done.

## Yang di-skip (YAGNI, tambah kalau kerasa)

- Dashboard "Open Loops" view + tombol done.
- Sub-langkah / active management (breakdown task).
- Tarik dari sumber luar (GitHub/email/Notion).
- Embedding recall buat `mark_done` (v1 pake keyword LIKE).
- Recurring loop / snooze.
