# Развёртывание на VPS с нуля

Инструкция для человека, который делает это впервые. Сервер: Linux (Ubuntu 24.04 LTS), 4 ядра, 8 ГБ памяти, от 40 ГБ диска. В результате сервис будет открываться по `https://<ваш-домен>` с настоящим сертификатом, а API и база не будут видны из интернета.

Команды ниже вводятся в терминале сервера. Строки, начинающиеся с `#`, — пояснения, их вводить не нужно.

## 1. Что понадобится

- VPS с публичным IP-адресом и доступом по SSH (логин `root` или пользователь с `sudo`).
- Домен или поддомен, например `collector.example.ru`, и доступ к его настройкам DNS.
- Архив проекта или доступ к репозиторию.
- Для реальных данных — копия базы (`.dump`), сделанная на машине, где данные уже загружены (шаг 7).

## 2. DNS

В панели регистратора домена создайте запись:

| Тип | Имя | Значение |
|---|---|---|
| A | `collector` (или `@` для самого домена) | IP-адрес сервера |

Проверка (через 5–30 минут): `ping collector.example.ru` должен показывать IP сервера. Без этого сертификат не выпустится.

## 3. Подключение и базовая защита сервера

```bash
ssh root@<IP-сервера>

# Обновления системы
apt update && apt upgrade -y

# Брандмауэр: открыты только SSH, HTTP и HTTPS
apt install -y ufw
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw enable        # на вопрос ответить y
ufw status
```

Важно: Docker публикует порты в обход `ufw`. Поэтому в production-конфигурации наружу опубликованы только 80 и 443 у Caddy — не добавляйте `ports:` другим сервисам.

## 4. Установка Docker

```bash
curl -fsSL https://get.docker.com | sh
docker --version
docker compose version     # нужна версия 2.24 или новее
```

## 5. Код проекта

```bash
mkdir -p /opt && cd /opt
# Вариант А: из репозитория
git clone <адрес-репозитория> collector
# Вариант Б: из архива (скопировать с своего компьютера: scp collector.tar.gz root@<IP>:/opt/)
#   mkdir collector && tar -xzf collector.tar.gz -C collector
cd /opt/collector
```

Архив для передачи делайте так (в нём нет `.env`, данных и копий БД):

```bash
git archive --format=tar.gz -o collector.tar.gz HEAD
```

## 6. Настройки: файл `.env`

```bash
cp .env.example .env

# Сгенерировать секреты и сразу вписать их в .env
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env
sed -i "s/^JWT_SECRET=.*/JWT_SECRET=$(openssl rand -hex 32)/" .env
sed -i "s/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -d '\/+=')/" .env
sed -i "s/^DOMAIN=.*/DOMAIN=collector.example.ru/" .env     # ← свой домен

chmod 600 .env
grep -E "^(DOMAIN|ADMIN_USERNAME|ADMIN_PASSWORD)=" .env     # запишите пароль администратора
```

Что обязательно должно быть в `.env`:

| Переменная | Что это | Откуда |
|---|---|---|
| `DOMAIN` | доменное имя сервера | шаг 2 |
| `POSTGRES_PASSWORD` | пароль базы, от 12 символов, только буквы и цифры | `openssl rand -hex 24` |
| `JWT_SECRET` | секрет подписи входа, от 32 символов | `openssl rand -hex 32` |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | первый администратор, пароль от 12 символов | придумать / сгенерировать |

`APP_ENV` и `CORS_ORIGINS` для production ставит `docker-compose.prod.yml` сам, `SEED_ON_START` по умолчанию выключен (в `.env.example` строка закомментирована — не раскомментируйте, если данные придут из копии БД). Если секрет слабый или из примера, API не запустится и напишет в логе, что именно исправить.

**Симуляция потока** (`SIM_ENABLED`) в production по умолчанию **выключена**: пока она идёт, режим «Сейчас» у всех пользователей переходит на модельное время. Для стенда, где жюри смотрит проигрывание суток, добавьте в `.env` строку `SIM_ENABLED=true` и перезапустите API (`dc up -d api`). Запуск — кнопкой «Симуляция» (инженер или администратор) или скриптом: `dc exec -e SIM_USERNAME=<логин> -e SIM_PASSWORD=<пароль> api python -m scripts.simulate_stream --day 2026-06-15 --speed 60`.

**Один процесс API.** Планировщик (APScheduler: такт симуляции и разметка исходов), брокер SSE и тикеты живут в процессе uvicorn, поэтому API запускается без `--workers` и в одном экземпляре. Не масштабируйте сервис `api` (`--scale api=2`): уведомления и такты симуляции разойдутся по процессам. Выключить фоновые задания — `SCHEDULER_ENABLED=false` (симуляция тогда тактируется опросом состояния из интерфейса).

## 7. Первый запуск

```bash
cd /opt/collector
alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml'   # чтобы не набирать каждый раз
echo "alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml'" >> ~/.bashrc

dc up -d --build        # первая сборка — 5–10 минут
dc ps                   # все сервисы Up, у db/api/frontend — (healthy)
dc logs api | tail -20  # «Создан администратор …», «Uvicorn running»
dc logs caddy | tail -20   # «certificate obtained successfully»
```

База пока пустая (без датчиков и прогнозов). Данные — одним из способов:

**А. Перенести готовую базу (реальные данные после ETL и ML).** На компьютере, где данные загружены, сделайте копию и отправьте на сервер:

```bash
# на своём компьютере, в папке проекта с запущенным demo-стеком.
# Копия пишется внутри контейнера и забирается docker cp: перенаправление «>» в Windows PowerShell 5.1
# перекодирует двоичный поток и портит файл
docker compose exec -T db pg_dump -U collector -d collector -Fc -f /tmp/collector.dump
docker compose cp db:/tmp/collector.dump ./collector.dump
docker compose exec -T db rm /tmp/collector.dump
scp collector.dump root@<IP-сервера>:/opt/collector/backups/
```

```bash
# на сервере
cd /opt/collector
./deploy/restore.sh backups/collector.dump
```

Копия 7,4 ГБ базы занимает около 200 МБ и восстанавливается примерно за 2–3 минуты (проверено: 11 млн прогнозов, 137 с); на диске после восстановления ≈ 4,5 ГБ. После восстановления API при старте блокирует демо-учётки с известными паролями (если они пришли в копии) и снова создаёт администратора из `.env`, если активного администратора нет.

**В. Срез реальных данных (июнь 2026, ≈ 137 МБ) — для стенда экспертов.** Меньше полной копии: прогнозы v3 с
исходами, события, отказы и метрики за месяц, без пользователей и журнала действий. Загрузка в production — только с
`--force` (сознательная замена данных):

```bash
dc cp expert_slice_20260601_20260630.zip api:/tmp/slice.zip
dc exec api python -m scripts.load_expert_slice /tmp/slice.zip --force
dc restart api
```

Подробно — [EXPERT_SLICE.md](EXPERT_SLICE.md). Решать, выставлять ли реальные прогнозы на публичный адрес, —
команде (данные обезличены, но это данные заказчика).

**Б. Демо-данные для показа.** Добавьте в `.env` строку `SEED_ON_START=true` и выполните `dc up -d api`. Демо-пользователи при этом создаются заблокированными.

## 8. Проверка

```bash
curl -s https://collector.example.ru/api/health
# {"status":"ok", ..., "database":"ok", "demo":false}

curl -sI https://collector.example.ru/ | grep -iE "strict-transport|content-security"
curl -s -o /dev/null -w "%{http_code}\n" http://collector.example.ru/     # 308 — перенаправление на https
```

Откройте `https://collector.example.ru` в браузере и войдите под `ADMIN_USERNAME` / `ADMIN_PASSWORD`. Кнопок «быстрый выбор роли» на экране входа быть не должно.

Проверка, что снаружи закрыто всё лишнее (с другого компьютера):

```bash
nc -zv <IP-сервера> 8000    # должно быть «refused» или таймаут (API)
nc -zv <IP-сервера> 5432    # то же (PostgreSQL)
```

После первого входа уберите пароль администратора из `.env` (он больше не нужен) и перезапустите API:

```bash
sed -i "s/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=/" .env
dc up -d api
```

## 9. Пользователи

Пользователей создаёт администратор сервера (пароль спросят с клавиатуры, от 12 символов):

```bash
dc exec api python -m app.bootstrap user orlova --role dispatcher_ods --full-name "Орлова М. (ОДС)"
dc exec api python -m app.bootstrap user ivanov --role dispatcher --scope 5773 --full-name "Иванов И. И."
dc exec api python -m app.bootstrap user smirnov --role technician --scope 5 --full-name "Смирнов О."
dc exec api python -m app.bootstrap user petrov --role engineer --full-name "Петров П. П."
dc exec api python -m app.bootstrap user sidorov --role manager --scope 5773 --full-name "Сидоров С. С."
```

`--role` и `--scope` можно повторять: роли складываются, область — объединение узлов (`--scope` — id района или
объекта-комплекса из справочника объектов: `dc exec db psql -U collector -d collector -c "select id, level, name
from objects where level <= 2 order by level, name"`). Ролям с областью (диспетчер района, техник, руководитель) без
`--scope` не видно ни одного датчика. Та же команда для существующего логина меняет пароль, роли и область и
разблокирует учётку; потом роли и области меняются и в интерфейсе: меню пользователя → «Пользователи и роли».
Роли, области и права — [SECURITY.md](SECURITY.md#роли-и-области-видимости).

### Вход через LDAP / Active Directory (вместо локальных паролей)

Учётные записи заказчика ведутся в AD — сервис может брать вход и роли оттуда. В `.env`:

```bash
LDAP_ENABLED=true
LDAP_URL=ldaps://dc01.corp.local:636        # в production — только ldaps:// или LDAP_START_TLS=true
LDAP_CA_FILE=/ldap-ca/corp-root.pem         # корневой сертификат каталога, если он не из системных
LDAP_BASE_DN=dc=corp,dc=local
LDAP_BIND_DN=cn=svc-collector,ou=service,dc=corp,dc=local   # сервисная учётка только для чтения
LDAP_BIND_PASSWORD=...
LDAP_USER_FILTER=(sAMAccountName={username})
LDAP_ROLE_GROUPS=admin:cn=collector-admins;dispatcher_ods:cn=collector-ods;dispatcher:cn=collector-dispatchers;technician:cn=collector-technicians;manager:cn=collector-managers;engineer:cn=collector-engineers
# Область видимости — группы AD по району и комплексам: «id объекта:группа»
LDAP_SCOPE_GROUPS=5773:cn=collector-district-south;5:cn=collector-complex-alpha;6:cn=collector-complex-beta
```

Группы лучше задавать полными DN через `;` (`admin:cn=collector-admins,ou=groups,dc=corp,dc=local;…`): короткая форма
`cn=…` совпадёт с группой с таким именем в любой ветке каталога. Роли складываются (пользователь в двух группах ролей получает обе), область — объединение узлов всех его групп
`LDAP_SCOPE_GROUPS`. Диспетчеру района, технику и руководителю нужна хотя бы одна группа области: без неё вход
отклоняется (закрыто по умолчанию — «видеть всё» по ошибке нельзя). Диспетчеру ОДС, инженеру и администратору
группа области не нужна. Роли и область обновляются из каталога при каждом входе.

Корневой сертификат положите рядом с проектом и подключите томом к сервису `api` (например, в
`docker-compose.override.yml`: `volumes: ["./ldap-ca:/ldap-ca:ro"]`). Перезапуск: `dc up -d api`.

Проверка: вход пользователем из нужной группы — роль и область в шапке совпадают с группами («Техник · объект Альфа»),
на «Схеме объектов» — только его комплекс; пользователь без группы — «Неверный
логин или пароль»; при недоступном каталоге вход закрыт (503), локальные пароли не принимаются. В
`dc exec db psql -U collector -d collector -c "select username, role, auth_source from users"` появятся профили с
`auth_source = ldap`. Если логин совпадает с локальной учётной записью (например, `admin`), вход через LDAP для него
отклоняется — назовите локального администратора иначе или заблокируйте его.

Как проверить без каталога заказчика — тестовый OpenLDAP (`ldaps://`, свой тестовый УЦ) из `deploy/ldap-test/`, только
на изолированном стенде:

```bash
docker compose -p ldaptest -f docker-compose.yml -f deploy/ldap-test/docker-compose.yml up -d --build
python deploy/ldap-test/check_ldap.py http://localhost:8000
docker compose -p ldaptest -f docker-compose.yml -f deploy/ldap-test/docker-compose.yml down -v
```

## 10. Резервные копии

Контейнер `backup` делает копию сразу при старте и затем раз в 24 часа, хранит 14 последних в `/opt/collector/backups`.

```bash
ls -lh backups/                          # список копий
dc logs backup | tail                    # «копия готова: …»
dc exec backup sh /backup.sh once        # сделать копию прямо сейчас
```

Копия на том же диске не спасёт при потере сервера. Раз в день увозите её на другой компьютер или в хранилище, например с рабочего компьютера:

```bash
rsync -av root@<IP-сервера>:/opt/collector/backups/ ./collector-backups/
```

## 11. Восстановление из копии

```bash
cd /opt/collector
ls -lh backups/
./deploy/restore.sh backups/collector_20260925_030000.dump
curl -s https://collector.example.ru/api/health
```

Скрипт останавливает API, заменяет таблицы данными из копии и запускает API снова. Вход, заявки и решения после этого — на момент копии.

## 12. Обновление версии

```bash
cd /opt/collector
dc exec backup sh /backup.sh once   # сначала копия
git pull                            # или распаковать новый архив поверх
dc up -d --build                    # миграции БД применяются при старте API
dc ps
curl -s https://collector.example.ru/api/health
```

Если что-то пошло не так: вернуть прежнюю версию кода (`git checkout <прежний-коммит>`), `dc up -d --build`, при необходимости — восстановление из копии (шаг 11).

## 13. Если что-то не работает

| Признак | Что проверить |
|---|---|
| `dc ps`: api перезапускается | `dc logs api` — «Небезопасные настройки…»: поправить `.env` по тексту ошибки |
| Браузер: ошибка сертификата | DNS указывает на сервер (`ping`), открыты 80 и 443 (`ufw status`), `dc logs caddy` |
| Страница открывается, вход «Неверный логин или пароль» | логин/пароль из `.env`; при повторных ошибках — 429 «Слишком много попыток», подождать 5 минут |
| Нет уведомлений (колокольчик серый) | `dc logs frontend`, `dc logs api`; SSE проходит через Caddy и Nginx без буферизации |
| Нет кнопки «Симуляция» | в production она выключена: `SIM_ENABLED=true` в `.env`, `dc up -d api`; кнопка видна только инженеру и администратору |
| Закончилось место | `df -h`; старые копии: уменьшить `BACKUP_KEEP` в `.env` и `dc up -d backup`; `docker system prune` |

Остановить всё: `dc down` (данные в томе `pgdata` сохраняются). Удалить вместе с данными: `dc down -v` — только если есть копия.
