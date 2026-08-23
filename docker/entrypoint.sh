#!/bin/sh
# Точка входа прод-контейнера: ждём БД → миграции → статика → (опц.) сиды → запуск.
set -eu

log() { echo "[entrypoint] $*"; }

DB_HOST="${DB_HOST:-${PGHOST:-db}}"
DB_PORT="${DB_PORT:-${PGPORT:-5432}}"

log "ожидание PostgreSQL ${DB_HOST}:${DB_PORT}"
python - "$DB_HOST" "$DB_PORT" <<'PY'
import socket, sys, time

host, port = sys.argv[1], int(sys.argv[2])
deadline = time.monotonic() + 60
while True:
    try:
        with socket.create_connection((host, port), timeout=3):
            break
    except OSError as exc:
        if time.monotonic() > deadline:
            sys.exit(f'PostgreSQL {host}:{port} недоступен: {exc}')
        time.sleep(1)
PY

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    log "migrate"
    python manage.py migrate --noinput
fi

if [ "${RUN_COLLECTSTATIC:-1}" = "1" ]; then
    log "collectstatic"
    python manage.py collectstatic --noinput --clear
fi

# Роли + суперпользователь admin/admin. Только для первичной инициализации стенда:
# в проде выставляйте RUN_SEED_ROLES=0 и заводите пользователей вручную.
if [ "${RUN_SEED_ROLES:-0}" = "1" ]; then
    log "seed_roles"
    python manage.py seed_roles
fi

log "старт: $*"
exec "$@"
