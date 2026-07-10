"""Consent OAuth sekali jalan per akun Google.

Prasyarat: taro data/credentials.json (OAuth client dari Google Cloud Console).
Jalanin:  uv run python scripts/google_auth.py kantor
          uv run python scripts/google_auth.py pribadi
Browser kebuka, login akun yg sesuai, izinin. Token kesimpen ke data/gcal_token_<label>.json.
"""

import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "pribadi"
    flow = InstalledAppFlow.from_client_secrets_file("data/credentials.json", SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    path = f"data/gcal_token_{label}.json"
    with open(path, "w") as f:
        f.write(creds.to_json())
    print(f"OK. Token akun '{label}' kesimpen di {path}")


if __name__ == "__main__":
    main()
