# DIAS · Деплой в production (Docker)

## Что поднимается

| Сервис    | Образ / сборка              | Роль                                                         |
|-----------|-----------------------------|--------------------------------------------------------------|
| `db`      | `postgres:16-alpine`        | PostgreSQL, данные в volume `pg_data`                        |
| `redis`   | `redis:7-alpine`            | Channel layer для WebSocket (`channels_redis`)               |
| `backend` | `Dockerfile` (multi-stage)  | Django + DRF на **daphne** (ASGI: HTTP + WS), non-root user  |
| `nginx`   | `docker/nginx/Dockerfile`   | Reverse proxy, gzip, rate limit, статика Django, готовый билд SPA |

Наружу торчит только `nginx` (порт `HTTP_PORT`, по умолчанию 80). Бэкенд, БД и Redis — во внутренней сети `dias_net`.

Миграции, `collectstatic` и ожидание готовности БД выполняет [docker/entrypoint.sh](docker/entrypoint.sh) при старте контейнера.

---

## 1. Подготовка сервера (Ubuntu 22.04/24.04, один раз)

```bash
# Docker Engine + compose plugin
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker

# Файрвол
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable
```

## 2. Код и окружение

```bash
git clone <repo-url> /opt/dias && cd /opt/dias
mkdir -p /opt/dias/frontend-dist   # сюда заливается билд фронта

cp .env.prod.example .env.prod
# сгенерировать SECRET_KEY:
docker run --rm python:3.12-slim python -c \
  "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits+'!@#%^&*(-_=+)') for _ in range(64)))"

nano .env.prod   # DJANGO_SECRET_KEY, PGPASSWORD, ALLOWED_HOSTS, CORS_ALLOWED_ORIGINS, WEBSOCKET_ALLOWED_ORIGINS
```

Обязательный минимум в `.env.prod`:

```ini
DEBUG=False
DJANGO_SECRET_KEY=<64 случайных символа>
ALLOWED_HOSTS=erp.example.com
PGDATABASE=dias
PGUSER=dias_user
PGPASSWORD=<сильный пароль>
CORS_ALLOWED_ORIGINS=https://erp.example.com
WEBSOCKET_ALLOWED_ORIGINS=https://erp.example.com
RUN_SEED_ROLES=1     # только на ПЕРВЫЙ запуск, потом верните 0
```

> При `DEBUG=False` Django откажется стартовать с дефолтным `DJANGO_SECRET_KEY` — это защита, а не баг.

## 3. Запуск — одна команда

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

Или короче, через Makefile:

```bash
make up
```

## 4. Проверка

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml ps      # все сервисы healthy
curl -i http://localhost/health/                                       # {"status":"ok","database":"up"}
curl -i http://localhost/api/docs/                                     # Swagger
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f backend
```

Доступно после старта:

- SPA — `http://<домен>/`
- API — `http://<домен>/api/`
- Swagger — `http://<домен>/api/docs/`
- Админка — `http://<домен>/admin/`
- WebSocket — `ws://<домен>/ws/operational?token=<JWT>`

## 5. Первичная инициализация (если `RUN_SEED_ROLES=0`)

```bash
make seed          # роли + суперпользователь admin/admin
make superuser     # либо свой суперпользователь
```

**Сразу смените пароль `admin`** после `seed_roles`, затем выставьте `RUN_SEED_ROLES=0`.

## 6. Обновление версии

```bash
cd /opt/dias
git pull
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
# миграции применятся автоматически в entrypoint; принудительно — make migrate
```

## 7. Эксплуатация

```bash
make logs          # логи всех сервисов
make ps            # статус + healthcheck
make backup        # дамп БД в ./backups/dias-<timestamp>.sql.gz
make restore FILE=backups/dias-20260823-120000.sql.gz
make down          # остановить (volume с данными остаётся)
make dbshell       # psql
```

Бэкап по расписанию (`crontab -e`):

```cron
0 3 * * * cd /opt/dias && make backup >> /var/log/dias-backup.log 2>&1
```

## 8. HTTPS

Проще всего терминировать TLS на хостовом Caddy/nginx или через Cloudflare, проксируя на `127.0.0.1:${HTTP_PORT}`.
Вариант с certbot на хосте:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d erp.example.com
```

После включения TLS в `.env.prod`:

```ini
SECURE_COOKIES=True                       # secure-cookies + HSTS + редирект на https
CORS_ALLOWED_ORIGINS=https://erp.example.com
WEBSOCKET_ALLOWED_ORIGINS=https://erp.example.com
HTTP_PORT=127.0.0.1:8080                  # контейнерный nginx только на loopback
```

и `make up`. Хостовой прокси должен передавать `X-Forwarded-Proto`, `Host`, а для `/ws/` — заголовки `Upgrade`/`Connection`.

## 9. Деплой фронтенда

SPA внутрь образа не собирается: nginx раздаёт каталог `FRONTEND_DIST` (по умолчанию `/opt/dias/frontend-dist`)
через bind-mount. Заливка билда — **без пересборки и рестарта контейнеров**, достаточно обновить файлы.

С машины разработчика:

```bash
npm run build

# rsync предпочтительнее scp: удаляет старые хэшированные ассеты и не льёт неизменившееся
rsync -az --delete build/ root@5.42.98.29:/opt/dias/frontend-dist/

# вариант на scp (старые файлы остаются мусором в каталоге):
scp -r build/* root@5.42.98.29:/opt/dias/frontend-dist/
```

Проверка после заливки:

```bash
curl -sI http://5.42.98.29/ | head -1                 # 200
curl -s http://5.42.98.29/ | grep -o '<title>[^<]*'   # ваш index.html, не шаблон
```

Важное:

- **Не заливайте билд в `/opt/dias/frontend/`** — это исходники из git; чужие файлы там сломают `git pull`.
  Каталог `frontend-dist/` в `.gitignore` и обновлениями репозитория не затрагивается.
- Права: nginx читает файлы от пользователя `nginx`. После `scp` из-под root обычно всё ок (644/755);
  при 403 — `chmod -R a+rX /opt/dias/frontend-dist`.
- `index.html` отдаётся с `Cache-Control: no-store`, `/assets/` и `/static/` — с длинным кэшем,
  поэтому новый билд подхватывается сразу, без сброса кэша у пользователей.
- `/static/` ищется сначала в билде фронта (CRA кладёт туда `js/`, `css/`), затем в статике Django (admin/jazzmin) —
  оба варианта сборки работают одновременно.
- Первый деплой можно сделать до запуска стека: каталог достаточно создать (`mkdir -p /opt/dias/frontend-dist`).

Фронт обращается к API по тому же origin (`/api/...`, `ws(s)://<домен>/ws/operational`) — CORS не задействован.
Если в билде зашит абсолютный URL бэкенда, укажите этот origin в `CORS_ALLOWED_ORIGINS` и `WEBSOCKET_ALLOWED_ORIGINS`.

## Диагностика

| Симптом                                   | Причина / что делать                                                              |
|-------------------------------------------|-----------------------------------------------------------------------------------|
| `backend` unhealthy, в логах `ImproperlyConfigured` | Не задан `DJANGO_SECRET_KEY` в `.env.prod`                              |
| `DisallowedHost`                          | Домен не добавлен в `ALLOWED_HOSTS`                                               |
| WebSocket рвётся на handshake (403)       | Origin фронта не указан в `WEBSOCKET_ALLOWED_ORIGINS` / `CORS_ALLOWED_ORIGINS`    |
| CSRF failed в админке по HTTPS            | Добавьте `CSRF_TRUSTED_ORIGINS=https://<домен>`                                   |
| Админка без стилей                        | `make collectstatic`, проверьте volume `static_files`                             |
| `429` на логине                           | Сработал `limit_req` nginx (10 запросов/мин на IP)                                |
| `/` отдаёт 403/404                        | Пустой или недоступный `FRONTEND_DIST`; залейте билд, проверьте права             |
| В браузере старый фронт                   | `scp` оставил прежние файлы — используйте `rsync --delete`                         |
