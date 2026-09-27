#!/bin/sh
# Восстановление базы из копии pg_dump (-Fc).
#   ./deploy/restore.sh backups/collector_20260925_030000.dump
# API на время восстановления останавливается; после старта он заново блокирует демо-учётки
# с известными паролями (APP_ENV=production), если они пришли вместе с копией.
set -eu

FILE=${1:?Укажите файл копии: ./deploy/restore.sh backups/<файл>.dump}
[ -f "$FILE" ] || { echo "Нет файла $FILE" >&2; exit 1; }
COMPOSE=${COMPOSE:-"docker compose -f docker-compose.yml -f docker-compose.prod.yml"}

echo "Останавливаю API..."
$COMPOSE stop api
echo "Восстанавливаю $FILE (существующие таблицы будут заменены)..."
$COMPOSE exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --exit-on-error' < "$FILE"
echo "Запускаю API..."
$COMPOSE start api
echo "Готово. Проверьте: curl -s https://\$DOMAIN/api/health"
