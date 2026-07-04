#!/usr/bin/env bash
# Auto-deploy bot: pull kalau ada update di origin/main, sync, pasang browser, restart.
# Dijalanin cron tiap menit. Idempotent — diam & keluar kalau udah up-to-date.
#
# Pasang (sekali):
#   chmod +x /root/ai-chief-of-staff/scripts/deploy.sh
#   crontab -e   → tambah baris:
#   * * * * * /root/ai-chief-of-staff/scripts/deploy.sh >> /var/log/chief-deploy.log 2>&1
set -euo pipefail

cd /root/ai-chief-of-staff

git fetch -q origin main
LOCAL=$(git rev-parse @)
REMOTE=$(git rev-parse '@{u}')
[ "$LOCAL" = "$REMOTE" ] && exit 0   # gak ada perubahan → keluar diam

NEW=$(git log -1 --format='%h %s' '@{u}' 2>/dev/null || echo "$REMOTE")
echo "[$(date -Is)] update ke $NEW — deploying"
# tandai lagi restart + commit apa yang masuk → muncul di activity feed & bikin status RESTARTING.
# lewat python (argv) biar pesan commit yang ada tanda kutip gak mecahin SQL. best-effort.
uv run python -c "import sys; from app.lib.events import log_event; log_event('deploy', sys.argv[1])" \
    "Update baru: $NEW — restarting" 2>/dev/null || true

git pull -q origin main
uv sync -q
uv run playwright install chromium -q   # idempotent; cepat kalau browser udah ada
systemctl restart ai-chief
echo "[$(date -Is)] done"
