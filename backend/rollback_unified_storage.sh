#!/usr/bin/env bash
set -euo pipefail
backup=${1:?backup directory required}
stage=${2:-/root/unified-store-stage-20260929}
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$stage:/var/www/lesson-tracker/backend"
systemctl stop emily-after-class.service emily-practice-resources.service emily-backend.service
if [ "${RESTORE_CURRENT_DATABASE:-1}" = 1 ]; then
  latest="$backup/rollback-current-$(date -u +%Y%m%dT%H%M%S%N)"
  /var/www/lesson-tracker/backend/venv/bin/python "$stage/export_legacy_storage.py" --database /var/lib/emily-store/core.sqlite3 --destination "$latest"
  /var/www/lesson-tracker/backend/venv/bin/python "$stage/restore_legacy_storage.py" --export "$latest" --root /var/www/lesson-tracker --followup /var/lib/emily-after-class/followup.sqlite3 --resources /var/lib/emily-practice/resources.sqlite3
fi
cp -a "$backup/app.py.before" /var/www/lesson-tracker/backend/app.py
cp -a "$backup/anti_scrape.conf.before" /etc/nginx/snippets/anti_scrape.conf
rm -f /etc/systemd/system/emily-backend.service.d/unified-storage.conf /etc/systemd/system/emily-after-class.service.d/unified-storage.conf /etc/systemd/system/emily-practice-resources.service.d/unified-storage.conf
systemctl disable --now emily-store-backup.timer || true
systemctl daemon-reload
nginx -t
systemctl start emily-backend.service emily-practice-resources.service emily-after-class.service
systemctl reload nginx
for timer in emily-practice-backup.timer emily-after-class-backup.timer; do
  if [ "$(cat "$backup/$timer.state")" = enabled ]; then systemctl enable --now "$timer"; fi
done
printf 'Rollback completed using current database data.\n'
