# Dashboard image. Sengaja TIDAK install deps bot (anthropic/playwright/telegram) —
# fastapi + google-api (Gmail draft lamaran via /api/drafts) doang.
FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30" \
    "google-auth>=2.50.0" "google-api-python-client>=2.198.0"

COPY app ./app

# Folder memory bot di-mount read-only ke sini lewat Coolify (lihat docs/dashboard.md)
ENV MEMORY_DIR=/memory
EXPOSE 8000

CMD ["uvicorn", "app.interfaces.dashboard:app", "--host", "0.0.0.0", "--port", "8000"]
