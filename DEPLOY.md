# DIAS — деплой на diass.tw1.ru

Схема: системный nginx на хосте (TLS, `diass.tw1.ru`, конфиг `/etc/nginx/sites-enabled/diyas`):

- `/` — SPA прямо с диска, `/opt/dias/frontend` (сюда заливает `Dias_Front/deploy.sh`)
- `/media/` — с диска, `/opt/dias/backend/media`
- `/static/admin|jazzmin|rest_framework|vendor/` — с диска, `/root/DIas_ERP/staticfiles`
- `/api/`, `/ws/` → `127.0.0.1:8080` → контейнер `dias-nginx` → Django (`dias-backend`, daphne)

Плюс PostgreSQL (том `dias_pg_data`) и Redis в контейнерах.

Все команды compose — **только** с `--env-file .env.prod`, иначе compose не найдёт `PG*`.
`make` подставляет его сам.

## Бэкенд

Первый запуск:

```bash
git clone https://github.com/Iskanov-Nurdan/DIas_ERP.git /root/DIas_ERP
cd /root/DIas_ERP
cp .env.prod.example .env.prod && nano .env.prod   # DJANGO_SECRET_KEY, PGPASSWORD
./deploy.sh
make cron-install                                  # ночные бэкапы (см. ниже)
```

Обновление — одной командой на сервере:

```bash
cd /root/DIas_ERP
./deploy.sh          # бэкап БД → код из origin/main → сборка → запуск → health-check
```

Берётся **только запушенный** `origin/main`. Если бэкап перед деплоем не получился,
деплой останавливается, не трогая ничего.

## Фронтенд (Dias_Front)

Собирается локально и заливается готовым билдом — из папки `Dias_Front`:

```bash
./deploy.sh          # npm ci → npm run build → заливка в /opt/dias/frontend на сервере
```

`git pull` скрипт не делает — собирает то, что лежит локально.
Перезапуск не нужен — системный nginx отдаёт файлы с диска.
Прод-сборка ходит в API по относительному `/api` (тот же домен, без CORS).

## Бэкапы

| Где | Когда | Что |
|---|---|---|
| сервер, `backups/` | каждую ночь в 03:00 UTC и перед каждым `deploy.sh` | `dias-*.sql.gz` (30 последних), `media-*.tar.gz` (14) |
| GitHub, приватный `Iskanov-Nurdan/becap_Dias` | каждую ночь сразу после серверного | `db/dias.sql` + `media/`; один коммит = одна ночь |
| локально, `~/dias-backups` | каждый день в 04:00 UTC — systemd-таймер на машине разработчика (`scripts/install-backup-timer.sh`, после переустановки системы — запустить заново) | копии файлов из `backups/` |

```bash
make backup          # бэкап вручную (с проверкой целостности)
make restore-test    # развернуть последний бэкап во временный postgres — боевую БД не трогает
make backup-github   # отправить копию на GitHub вручную
make cron-install    # (пере)установить ночной cron — /etc/cron.d/dias-backup
tail backups/cron.log
```

На GitHub сервер ходит отдельным deploy key `/root/.ssh/dias_backups_deploy` с правом
записи только в `becap_Dias`. Нужен бэкап на прошлую дату — коммит этого дня в `becap_Dias`.

Том `dias_pg_data` объявлен в compose как `external` — `docker compose down -v` его
не удаляет. Не запускайте `docker volume rm dias_pg_data` / `docker volume prune -a`.

## Проверка

```bash
make ps                                   # все сервисы healthy
curl -s http://127.0.0.1:8080/health/     # на сервере: {"status": "ok", "database": "up"}
make doctor                               # статус, свежие ошибки, диск
```

Снаружи `https://diass.tw1.ru/health/` JSON **не** вернёт — системный nginx проксирует
в бэкенд только `/api/` и `/ws/`, остальное уходит в SPA. Снаружи живость API видна по
`curl -s -o /dev/null -w '%{http_code}' https://diass.tw1.ru/api/` → `401` (жив, просит логин).
(В nginx есть блок `api.diass.tw1.ru`, но DNS-записи у этого поддомена нет.)

## Частые проблемы

| Симптом | Причина |
|---|---|
| `required variable PGDATABASE is missing` | compose запущен без `--env-file .env.prod` |
| `exec /app/docker/entrypoint.sh: no such file or directory` | CRLF в `entrypoint.sh` (см. `.gitattributes`) |
| CORS-ошибка с localhost:3000 | origin не указан в `CORS_ALLOWED_ORIGINS` в `.env.prod` |
| `CSRF verification failed` в админке | нет `CSRF_TRUSTED_ORIGINS=https://diass.tw1.ru` |
| Белый экран на `/` | пустая `/opt/dias/frontend` — залейте билд (`Dias_Front/deploy.sh`) |
| 404 на `/static/js/*`, хотя файлы есть | нет прав на чтение — `chmod -R a+rX /opt/dias/frontend` (deploy.sh делает сам) |
| `external volume "dias_pg_data" not found` | чистый сервер — `docker volume create dias_pg_data` (deploy.sh делает сам) |
