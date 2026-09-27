# Развёртывание: кратко

Пошаговая инструкция для VPS с нуля — [DEPLOY_VPS.md](DEPLOY_VPS.md). Модель безопасности — [SECURITY.md](SECURITY.md).

| Файл | Что делает |
|---|---|
| `docker-compose.yml` | demo-режим: db, api, frontend; API и БД на 127.0.0.1 |
| `docker-compose.prod.yml` | оверлей production: `APP_ENV=production`, обязательные секреты из `.env`, порты api/db/frontend не публикуются, `caddy` (TLS, 80/443), `backup` |
| `deploy/Caddyfile` | TLS Let's Encrypt для `DOMAIN`, HSTS, проксирование во frontend (Nginx) без буферизации для SSE |
| `frontend/docker/nginx.conf`, `security-headers.conf` | статика, прокси `/api`, CSP и заголовки, лимиты тела и частоты входа, журнал без query-строк |
| `deploy/backup.sh` | `pg_dump -Fc` раз в `BACKUP_INTERVAL_HOURS`, ротация `BACKUP_KEEP` копий в `./backups` |
| `deploy/restore.sh` | восстановление из копии с остановкой API |
| `backend/entrypoint.sh` | проверка настроек → миграции → сид (если включён) → блокировка демо-учёток и администратор → uvicorn |

```bash
cp .env.example .env    # DOMAIN, POSTGRES_PASSWORD, JWT_SECRET, ADMIN_PASSWORD — свои
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Цепочка запроса в production: клиент → Caddy (443, TLS) → Nginx фронтенда (статика, `/api`) → API (uvicorn, `--proxy-headers`) → PostgreSQL. Caddy не доверяет входящему `X-Forwarded-For` и ставит адрес клиента сам, Nginx передаёт его в API, поэтому в `audit_log.ip` попадает реальный IP.

LDAP — [SECURITY.md](SECURITY.md#ldap-заглушка).
