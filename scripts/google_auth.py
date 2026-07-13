"""Consent OAuth sekali jalan per akun Google.

Prasyarat: taro data/credentials.json (OAuth client dari Google Cloud Console).
Jalanin:  uv run python scripts/google_auth.py kantor            # calendar (default)
          uv run python scripts/google_auth.py pribadi
          uv run python scripts/google_auth.py pribadi gmail     # gmail (draft lamaran)
Browser kebuka, login akun yg sesuai, izinin. Token kesimpen ke data/<service>_token_<label>.json.
Token per-service SENGAJA dipisah — gabung scope = token lama minta consent ulang.
"""

import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SERVICES = {
    "calendar": {
        "scopes": ["https://www.googleapis.com/auth/calendar.events"],
        "token": "data/gcal_token_{label}.json",  # nama lama, jangan diganti (calendar_tool glob)
    },
    "gmail": {
        "scopes": ["https://www.googleapis.com/auth/gmail.compose"],  # bikin draft doang, gak baca inbox
        "token": "data/gmail_token_{label}.json",
    },
}


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "pribadi"
    service = sys.argv[2] if len(sys.argv) > 2 else "calendar"
    if service not in SERVICES:
        sys.exit(f"Service '{service}' gak dikenal. Pilihan: {', '.join(SERVICES)}")
    cfg = SERVICES[service]
    flow = InstalledAppFlow.from_client_secrets_file("data/credentials.json", cfg["scopes"])
    creds = flow.run_local_server(port=0, prompt="consent")
    path = cfg["token"].format(label=label)
    with open(path, "w") as f:
        f.write(creds.to_json())
    print(f"OK. Token {service} akun '{label}' kesimpen di {path}")


if __name__ == "__main__":
    main()
