"""Google Calendar — baca beberapa akun sekaligus (auto-scan data/gcal_token_*.json).

Label akun = bagian '*' di nama file: gcal_token_kantor.json -> 'kantor'.
Nambah akun = jalanin scripts/google_auth.py <label> lagi, gak usah ubah kode.
"""

import glob
import json
import os
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))
TOKEN_GLOB = "data/gcal_token_*.json"
SEEN_PATH = "data/gcal_seen.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def _services():
    """Yield (label, service) per akun. Refresh + persist token kalau kadaluarsa."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    for path in sorted(glob.glob(TOKEN_GLOB)):
        label = os.path.basename(path)[len("gcal_token_"):-len(".json")]
        creds = Credentials.from_authorized_user_file(path, SCOPES)
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
            with open(path, "w") as f:
                f.write(creds.to_json())
        yield label, build("calendar", "v3", credentials=creds, cache_discovery=False)


def _parse(label: str, e: dict) -> dict:
    start_raw = e.get("start", {})
    if "dateTime" in start_raw:
        start = datetime.fromisoformat(start_raw["dateTime"]).astimezone(WIB)
        timed = True
    else:  # all-day
        start = datetime.fromisoformat(start_raw.get("date", "1970-01-01")).replace(tzinfo=WIB)
        timed = False

    # status RSVP gua sendiri (kalau gua diundang)
    needs_action = False
    for att in e.get("attendees", []):
        if att.get("self") and att.get("responseStatus") == "needsAction":
            needs_action = True
            break

    return {
        "id": e.get("id", ""),
        "label": label,
        "summary": e.get("summary", "(tanpa judul)"),
        "start": start,
        "timed": timed,
        "gmeet": e.get("hangoutLink"),
        "needs_action": needs_action,
    }


def fetch_events(time_min: datetime, time_max: datetime) -> list:
    """Gabungan event dari semua akun, terurut by waktu mulai."""
    events = []
    for label, svc in _services():
        resp = svc.events().list(
            calendarId="primary",
            timeMin=time_min.astimezone(timezone.utc).isoformat(),
            timeMax=time_max.astimezone(timezone.utc).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        ).execute()
        events.extend(_parse(label, e) for e in resp.get("items", []))
    events.sort(key=lambda x: x["start"])
    return events


def load_seen() -> dict:
    try:
        with open(SEEN_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_seen(seen: dict):
    os.makedirs("data", exist_ok=True)
    with open(SEEN_PATH, "w") as f:
        json.dump(seen, f)


class CalendarTool:
    name = "calendar"
    description = (
        "Liat agenda kalender Rendy (gabungan semua akun: kantor + pribadi). "
        "Input kosong = agenda hari ini. 'range:N' = N hari ke depan (contoh range:3)."
    )

    def run(self, input: str = "", user_id: str = "") -> str:
        if not glob.glob(TOKEN_GLOB):
            return "Belum ada akun Google yang tersambung. Jalanin scripts/google_auth.py dulu."
        q = (input or "").strip().lower()
        now = datetime.now(WIB)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

        if q.startswith("range:"):
            try:
                days = max(1, int(q.split(":", 1)[1]))
            except ValueError:
                days = 1
        else:
            days = 1
        time_max = start_of_day + timedelta(days=days)

        try:
            events = fetch_events(start_of_day, time_max)
        except FileNotFoundError:
            return "Belum ada akun Google yang tersambung. Jalanin scripts/google_auth.py dulu."
        except Exception as e:
            return f"Error baca kalender: {e}"

        if not events:
            return "Gak ada agenda." if days == 1 else f"Gak ada agenda {days} hari ke depan."
        return "\n".join(self._fmt(e) for e in events)

    @staticmethod
    def _fmt(e: dict) -> str:
        if e["timed"]:
            when = e["start"].strftime("%a %d/%m %H:%M")
        else:
            when = e["start"].strftime("%a %d/%m") + " (seharian)"
        extra = []
        if e["gmeet"]:
            extra.append("gmeet")
        if e["needs_action"]:
            extra.append("belum RSVP")
        tail = f" ({', '.join(extra)})" if extra else ""
        return f"{when} · {e['summary']} [{e['label']}]{tail}"
