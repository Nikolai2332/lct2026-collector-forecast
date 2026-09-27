#!/bin/sh
set -e

# Слабые секреты при APP_ENV=production — сразу отказ, до миграций и сида
python -m app.bootstrap check

echo "Применяю миграции..."
alembic upgrade head

if [ "${SEED_ON_START:-true}" = "true" ]; then
  echo "Проверяю демо-данные..."
  python -m app.seed --scale "${SEED_SCALE:-demo}"
fi

# production: блокировка демо-учёток с известными паролями, начальный администратор из ADMIN_*
python -m app.bootstrap prepare

# Журнал доступа uvicorn выключен: он пишет адреса с query-строкой (тикеты SSE, поисковые запросы).
# Запросы журналирует Nginx без query, действия пользователей — audit_log.
# X-Forwarded-For принимаем от прокси внутри docker-сети. В production порт API наружу не публикуется,
# поэтому подделать заголовок в обход Nginx нельзя (docs/SECURITY.md)
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers \
  --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}" --no-server-header --no-access-log
