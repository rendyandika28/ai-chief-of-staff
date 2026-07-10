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
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def _build_service(path: str):
    """Bangun service dari 1 token file. Refresh + persist kalau kadaluarsa."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(path, SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        with open(path, "w") as f:
            f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _services():
    """Yield (label, service) per akun."""
    for path in sorted(glob.glob(TOKEN_GLOB)):
        label = os.path.basename(path)[len("gcal_token_"):-len(".json")]
        yield label, _build_service(path)


def _service_for(label: str):
    """Service buat 1 akun (by label). None kalau token gak ada."""
    path = f"data/gcal_token_{label}.json"
    return _build_service(path) if os.path.exists(path) else None


def _parse(label: str, e: dict) -> dict:
    start_raw = e.get("start", {})
    if "dateTime" in start_raw:
        start = datetime.fromisoformat(start_raw["dateTime"]).astimezone(WIB)
        timed = True
    else:  # all-day
        start = datetime.fromisoformat(start_raw.get("date", "1970-01-01")).replace(tzinfo=WIB)
        timed = False

    end_raw = e.get("end", {})
    end = (datetime.fromisoformat(end_raw["dateTime"]).astimezone(WIB)
           if "dateTime" in end_raw else None)

    # status RSVP gua sendiri (kalau gua diundang)
    needs_action = False
    for att in e.get("attendees", []):
        if att.get("self") and att.get("responseStatus") == "needsAction":
            needs_action = True
            break

    org = e.get("organizer", {})
    guests = [
        att.get("displayName") or att.get("email", "")
        for att in e.get("attendees", []) if not att.get("resource")
    ]

    return {
        "id": e.get("id", ""),
        "label": label,
        "summary": e.get("summary", "(tanpa judul)"),
        "start": start,
        "end": end,
        "timed": timed,
        "gmeet": e.get("hangoutLink"),
        "location": e.get("location"),
        "organizer": org.get("displayName") or org.get("email", ""),
        "guests": [g for g in guests if g],
        "needs_action": needs_action,
    }


def format_invite_card(e: dict) -> str:
    """Kartu undangan buat notif proaktif (detail lengkap, bukan LLM-phrased)."""
    if e["timed"]:
        when = e["start"].strftime("%a %d/%m/%Y · %H:%M")
        if e.get("end"):
            when += "–" + e["end"].strftime("%H:%M")
        when += " WIB"
    else:
        when = e["start"].strftime("%a %d/%m/%Y") + " (seharian)"

    lines = ["📅 Undangan baru — belum di-RSVP", "", e["summary"], f"🗓 {when}"]
    if e.get("organizer"):
        lines.append(f"👤 Dari: {e['organizer']}")
    guests = e.get("guests") or []
    if guests:
        shown = ", ".join(guests[:4])
        if len(guests) > 4:
            shown += f" (+{len(guests) - 4} lagi)"
        lines.append(f"👥 Tamu: {shown}")
    if e.get("gmeet"):
        lines.append(f"🔗 {e['gmeet']}")
    elif e.get("location"):
        lines.append(f"📍 {e['location']}")
    lines.append(f"📁 akun: {e['label']}")
    return "\n".join(lines)


def set_rsvp(label: str, event_id: str, status: str, svc=None) -> bool:
    """Set responseStatus gua (accepted/declined/tentative) di event, notify organizer.
    Return True kalau kesave. svc bisa diinject buat test."""
    if status not in ("accepted", "declined", "tentative"):
        return False
    svc = svc or _service_for(label)
    if svc is None:
        return False
    ev = svc.events().get(calendarId="primary", eventId=event_id).execute()
    attendees = ev.get("attendees", [])
    changed = False
    for att in attendees:
        if att.get("self"):
            att["responseStatus"] = status
            changed = True
    if not changed:
        return False
    svc.events().patch(
        calendarId="primary", eventId=event_id,
        body={"attendees": attendees}, sendUpdates="all",
    ).execute()
    return True


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


def _demo():
    """Self-check: python -m app.tools.calendar_tool — no network."""
    now = datetime.now(WIB)
    e = {
        "id": "abc", "label": "kantor", "summary": "Meeting Test (1)",
        "start": now.replace(hour=17, minute=0), "end": now.replace(hour=18, minute=0),
        "timed": True, "gmeet": "https://meet.google.com/uem-ghgb-xfn",
        "location": None, "organizer": "Rendy Andika",
        "guests": ["Rendy Andika", "Budi", "Sisi", "Dewi", "Eka"], "needs_action": True,
    }
    card = format_invite_card(e)
    assert "Meeting Test (1)" in card
    assert "17:00–18:00 WIB" in card
    assert "Dari: Rendy Andika" in card
    assert "+1 lagi" in card  # 5 guests, show 4 + (+1 lagi)
    assert "meet.google.com/uem-ghgb-xfn" in card
    assert "akun: kantor" in card

    class _FakeEvents:
        def __init__(self): self.patched = None
        def get(self, calendarId, eventId):
            body = {"attendees": [
                {"email": "org@x.com", "responseStatus": "accepted"},
                {"email": "rendy@x.com", "self": True, "responseStatus": "needsAction"},
            ]}
            return type("R", (), {"execute": lambda s=None: body})()
        def patch(self, calendarId, eventId, body, sendUpdates):
            self.patched = body
            return type("R", (), {"execute": lambda s=None: {}})()

    class _FakeSvc:
        def __init__(self): self._e = _FakeEvents()
        def events(self): return self._e

    svc = _FakeSvc()
    assert set_rsvp("kantor", "abc", "accepted", svc=svc) is True
    self_att = [a for a in svc._e.patched["attendees"] if a.get("self")][0]
    assert self_att["responseStatus"] == "accepted", "self RSVP must update"
    assert set_rsvp("kantor", "abc", "bogus", svc=svc) is False, "invalid status rejected"

    print("calendar_tool self-check OK")


if __name__ == "__main__":
    _demo()
