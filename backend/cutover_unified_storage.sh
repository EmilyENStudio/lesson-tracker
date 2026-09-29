#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
stage=/root/unified-store-stage-20260929
backup=/root/emily-practice-backups/unified-store-cutover-20260929
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$stage:/var/www/lesson-tracker/backend"
py=/var/www/lesson-tracker/backend/venv/bin/python
test "$(sha256sum /var/www/lesson-tracker/backend/app.py | cut -d' ' -f1)" = b2c2d74ff8905fc555b6f547777309fce6a3cc3bd43e24fcffe0effe0bdd709d
test ! -e /var/lib/emily-store/core.sqlite3
test ! -e "$backup"
for unit in emily-backend emily-after-class emily-practice-resources; do
  test ! -e "/etc/systemd/system/$unit.service.d/unified-storage.conf"
  systemctl is-active --quiet "$unit.service"
done
install -d -m 700 "$backup"
cp -a /var/www/lesson-tracker/backend/app.py "$backup/app.py.before"
cp -a /etc/nginx/snippets/anti_scrape.conf "$backup/anti_scrape.conf.before"
for timer in emily-practice-backup.timer emily-after-class-backup.timer; do
  systemctl is-enabled "$timer" > "$backup/$timer.state" || true
done
sha256sum /var/www/lesson-tracker/home-v1.html /var/www/lesson-tracker/after-class.js /opt/emily-after-class/model.py /opt/emily-after-class/service.py /opt/emily-practice-resources/service.py > "$backup/protected.sha256"
stat -c '%a %u %g %n' /var/www/lesson-tracker/backend/app.py /etc/nginx/snippets/anti_scrape.conf > "$backup/code-permissions.txt"
"$py" "$stage/patch_storage_nginx.py" "$backup/anti_scrape.conf.before" "$stage/anti_scrape.conf"
switched=0
recover() {
  status=$?
  trap - ERR
  RESTORE_CURRENT_DATABASE="$switched" bash "$stage/rollback_unified_storage.sh" "$backup" "$stage"
  exit "$status"
}
trap recover ERR
systemctl stop emily-after-class.service emily-practice-resources.service emily-backend.service
"$py" "$stage/snapshot_storage.py" --root /var/www/lesson-tracker --destination "$backup/source" --source-db /var/lib/emily-after-class/followup.sqlite3 --source-db /var/lib/emily-practice/resources.sqlite3
install -d -m 2770 -o emily-practice -g emily-practice /var/lib/emily-store
"$py" "$stage/migrate_operational_store.py" --root "$backup/source/site" --database /var/lib/emily-store/core.sqlite3 --source-db "$backup/source/followup.sqlite3" --source-db "$backup/source/resources.sqlite3"
"$py" "$stage/audit_unified_storage.py" --database /var/lib/emily-store/core.sqlite3
chown emily-practice:emily-practice /var/lib/emily-store/core.sqlite3
chmod 660 /var/lib/emily-store/core.sqlite3
switched=1
# Recheck live code immediately before replacement to catch overlapping work.
cmp -s /var/www/lesson-tracker/backend/app.py "$backup/app.py.before"
cmp -s /etc/nginx/snippets/anti_scrape.conf "$backup/anti_scrape.conf.before"
cp "$stage/app.py" /var/www/lesson-tracker/backend/app.py.storage-new
chmod --reference="$backup/app.py.before" /var/www/lesson-tracker/backend/app.py.storage-new
chown --reference="$backup/app.py.before" /var/www/lesson-tracker/backend/app.py.storage-new
mv /var/www/lesson-tracker/backend/app.py.storage-new /var/www/lesson-tracker/backend/app.py
install -m 644 "$stage/operational_store.py" /var/www/lesson-tracker/backend/operational_store.py
install -m 644 "$stage/audit_unified_storage.py" /var/www/lesson-tracker/backend/audit_unified_storage.py
cp "$stage/anti_scrape.conf" /etc/nginx/snippets/anti_scrape.conf
chmod --reference="$backup/anti_scrape.conf.before" /etc/nginx/snippets/anti_scrape.conf
chown --reference="$backup/anti_scrape.conf.before" /etc/nginx/snippets/anti_scrape.conf
install -d -m 755 /etc/systemd/system/emily-backend.service.d /etc/systemd/system/emily-after-class.service.d /etc/systemd/system/emily-practice-resources.service.d /opt/emily-store
install -m 644 "$stage/backend-storage.conf" /etc/systemd/system/emily-backend.service.d/unified-storage.conf
install -m 644 "$stage/followup-storage.conf" /etc/systemd/system/emily-after-class.service.d/unified-storage.conf
install -m 644 "$stage/resource-storage.conf" /etc/systemd/system/emily-practice-resources.service.d/unified-storage.conf
install -m 644 "$stage/backup_unified_store.py" /opt/emily-store/backup_unified_store.py
install -m 644 "$stage/emily-store-backup.service" /etc/systemd/system/emily-store-backup.service
install -m 644 "$stage/emily-store-backup.timer" /etc/systemd/system/emily-store-backup.timer
nginx -t
systemctl daemon-reload
systemctl start emily-backend.service emily-practice-resources.service emily-after-class.service
for retry in 1 2 3 4 5; do
  if curl --max-time 3 -fsS http://127.0.0.1:5000/api/health; then break; fi
  if [ "$retry" = 5 ]; then false; fi
  sleep 1
done
systemctl is-active --quiet emily-backend.service emily-practice-resources.service emily-after-class.service
systemctl reload nginx
systemctl start emily-store-backup.service
systemctl enable --now emily-store-backup.timer
systemctl disable --now emily-practice-backup.timer emily-after-class-backup.timer
sha256sum -c "$backup/protected.sha256"
"$py" "$stage/audit_unified_storage.py" --database /var/lib/emily-store/core.sqlite3
trap - ERR
printf '\nUnified operational storage cutover completed.\n'
