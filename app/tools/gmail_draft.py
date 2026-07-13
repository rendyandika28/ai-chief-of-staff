"""Gmail draft — bikin draft lamaran (cover letter + attach CV) di Gmail pribadi Rendy.

Semi-auto by design: draft doang, Rendy yang review + Send manual dari Gmail.
Scope gmail.compose = cuma bisa bikin draft, gak bisa baca inbox.
Token: data/gmail_token_pribadi.json (scripts/google_auth.py pribadi gmail).
CV: path dari profile resume_path (memory/profile.json).
"""

import base64
import os
from email.message import EmailMessage

TOKEN_PATH = "data/gmail_token_pribadi.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


def subject_for(job: dict, profile: dict) -> str:
    """"Application for {title} — {full_name}". build_cover_letter cuma bikin body."""
    name = (profile or {}).get("contact", {}).get("full_name", "Rendy Andika")
    return f"Application for {job.get('title', 'the position')} — {name}"


def build_mime(to: str, subject: str, body: str, cv_path: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if cv_path and os.path.exists(cv_path):
        with open(cv_path, "rb") as f:
            msg.add_attachment(f.read(), maintype="application", subtype="pdf",
                               filename=os.path.basename(cv_path))
    return msg


def _service():
    """Pola sama kayak calendar_tool._build_service — refresh + persist token."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def create_draft(to: str, subject: str, body: str, cv_path: str = "", service=None) -> str:
    """Bikin draft di Gmail, return draft id. service param buat test (mock)."""
    if not to:
        raise ValueError("Tujuan email kosong — job ini gak punya email, apply manual via URL.")
    svc = service or _service()
    raw = base64.urlsafe_b64encode(build_mime(to, subject, body, cv_path).as_bytes()).decode()
    draft = svc.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    return draft["id"]


def _demo():
    """Self-check MIME + create_draft (API di-mock). `python -m app.tools.gmail_draft`."""
    import tempfile

    # build_mime: to/subject/body kebentuk; CV keattach kalau file ada
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(b"%PDF-1.4 fake")
        f.flush()
        msg = build_mime("hr@foo.com", "Application for X", "Dear HM", f.name)
        atts = [p.get_filename() for p in msg.iter_attachments()]
        assert msg["To"] == "hr@foo.com" and msg["Subject"] == "Application for X"
        assert atts == [os.path.basename(f.name)], atts
    # tanpa CV → tetep jalan, gak ada attachment
    assert not list(build_mime("a@b.c", "s", "b").iter_attachments())

    # subject_for: pake title job + nama dari profile
    s = subject_for({"title": "Frontend Engineer"}, {"contact": {"full_name": "Rendy Andika"}})
    assert s == "Application for Frontend Engineer — Rendy Andika", s

    # create_draft: raw base64 valid, id balik dari API (mock); to kosong → error
    class _FakeSvc:
        def users(self): return self
        def drafts(self): return self
        def create(self, userId, body):
            raw = body["message"]["raw"]
            base64.urlsafe_b64decode(raw)  # harus decodable
            self._ok = True
            return self
        def execute(self): return {"id": "draft-123"}
    assert create_draft("hr@foo.com", "s", "b", service=_FakeSvc()) == "draft-123"
    try:
        create_draft("", "s", "b", service=_FakeSvc())
        assert False, "harusnya ValueError"
    except ValueError:
        pass

    print("OK: mime+attach · subject · create_draft(mock) · reject-empty-to")


if __name__ == "__main__":
    _demo()
