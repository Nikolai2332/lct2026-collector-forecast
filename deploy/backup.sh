#!/bin/sh
# Резервная копия PostgreSQL по расписанию (контейнер backup в docker-compose.prod.yml).
# pg_dump в формате custom (-Fc) раз в BACKUP_INTERVAL_HOURS часов, хранятся BACKUP_KEEP последних копий.
# Разовый запуск: docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backup sh /backup.sh once
set -eu

INTERVAL=$(( ${BACKUP_INTERVAL_HOURS:-24} * 3600 ))
KEEP=${BACKUP_KEEP:-14}
DIR=/backups
umask 077

run_backup() {
  file="$DIR/${PGDATABASE}_$(date +%Y%m%d_%H%M%S).dump"
  if pg_dump -Fc -f "$file.part"; then
    mv "$file.part" "$file"
    echo "$(date -Iseconds) копия готова: $file ($(du -h "$file" | cut -f1))"
  else
    rm -f "$file.part"
    echo "$(date -Iseconds) ОШИБКА резервного копирования" >&2
    return 1
  fi
  # Ротация: удаляем всё старше KEEP последних
  ls -1t "$DIR"/*.dump 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f --
}

if [ "${1:-}" = "once" ]; then
  run_backup
  exit $?
fi

echo "Резервное копирование: каждые ${BACKUP_INTERVAL_HOURS:-24} ч, хранится $KEEP копий в $DIR"
while true; do
  run_backup || true
  sleep "$INTERVAL"
done
