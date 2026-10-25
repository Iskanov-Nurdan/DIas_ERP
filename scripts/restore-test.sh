#!/usr/bin/env bash
# Проверка, что бэкап реально восстанавливается. Боевую БД НЕ трогает:
# поднимает временный postgres, заливает в него дамп, считает таблицы и записи, удаляет.
#   ./scripts/restore-test.sh                              — последний бэкап
#   ./scripts/restore-test.sh backups/dias-....sql.gz      — конкретный
set -euo pipefail

cd "$(dirname "$0")/.."

log()  { echo -e "\033[1;34m[restore-test]\033[0m $*"; }
fail() { echo -e "\033[1;31m[restore-test] ОШИБКА:\033[0m $*" >&2; exit 1; }

file="${1:-$(ls -1t backups/dias-*.sql.gz 2>/dev/null | head -1)}"
[ -n "$file" ] && [ -f "$file" ] || fail "бэкап не найден"

name="dias-restore-test-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT

log "временный postgres $name"
docker run -d --name "$name" -e POSTGRES_PASSWORD=test -e POSTGRES_DB=restore_test postgres:16-alpine >/dev/null
for i in $(seq 1 30); do
    docker exec "$name" pg_isready -U postgres -d restore_test >/dev/null 2>&1 && break
    sleep 1
done

log "заливка $file"
# Роль владельца из боевой БД во временном postgres не существует — без неё дамп ругается на OWNER TO.
gunzip -c "$file" | docker exec -i "$name" psql -q -U postgres -d restore_test \
    -v ON_ERROR_STOP=0 --set=client_min_messages=warning 2>&1 >/dev/null | grep -v 'role .* does not exist' || true

tables="$(docker exec "$name" psql -tA -U postgres -d restore_test -c \
    "select count(*) from information_schema.tables where table_schema='public'")"
[ "$tables" -gt 0 ] || fail "после восстановления в БД 0 таблиц"

log "таблиц: $tables; записей в крупнейших:"
docker exec "$name" psql -q -U postgres -d restore_test -c "analyze" >/dev/null
docker exec "$name" psql -U postgres -d restore_test -c \
    "select relname as table, n_live_tup as rows from pg_stat_user_tables order by n_live_tup desc limit 10"
log "ок — бэкап восстанавливается"
