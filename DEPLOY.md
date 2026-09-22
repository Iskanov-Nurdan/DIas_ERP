# DIAS — деплой на diass.tw1.ru

Схема: системный nginx (TLS, `diass.tw1.ru`) → `127.0.0.1:8080` → контейнер `dias-nginx`
→ SPA из `frontend-dist/` + Django (`dias-backend`, daphne) + PostgreSQL + Redis.

Все команды compose — **только** с `--env-file .env.prod`, иначе compose не найдёт `PG*`.
`make` подставляет его сам.

## Бэкенд

Первый запуск:

```bash
git clone https://github.com/Iskanov-Nurdan/DIas_ERP.git /root/DIas_ERP
cd /root/DIas_ERP
cp .env.prod.example .env.prod && nano .env.prod   # DJANGO_SECRET_KEY, PGPASSWORD
make up
```

Обновление:

```bash
cd /root/DIas_ERP
make backup          # дамп БД в backups/ — перед каждым обновлением
git pull
make up
```

## Фронтенд (Dias_Front)

Собирается локально и заливается готовым билдом:

```bash
npm ci
npm run build
scp -r build/* root@<IP сервера>:/root/DIas_ERP/frontend-dist/
```

Перезапуск не нужен — nginx отдаёт файлы из смонтированной папки.
Прод-сборка ходит в API по относительному `/api` (тот же домен, без CORS).

## Проверка

```bash
make ps                                   # все сервисы healthy
curl -s https://diass.tw1.ru/health/      # {"status": "ok", "database": "up"}
make doctor                               # статус, свежие ошибки, диск
```

## Частые проблемы

| Симптом | Причина |
|---|---|
| `required variable PGDATABASE is missing` | compose запущен без `--env-file .env.prod` |
| `exec /app/docker/entrypoint.sh: no such file or directory` | CRLF в `entrypoint.sh` (см. `.gitattributes`) |
| CORS-ошибка с localhost:3000 | origin не указан в `CORS_ALLOWED_ORIGINS` в `.env.prod` |
| `CSRF verification failed` в админке | нет `CSRF_TRUSTED_ORIGINS=https://diass.tw1.ru` |
| Белый экран на `/` | пустая `frontend-dist/` — залейте билд |
