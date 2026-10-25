#!/usr/bin/env bash
# Копия БД + media в приватный GitHub-репозиторий (запускать на сервере из /root/DIas_ERP;
# cron делает это каждую ночь сразу после scripts/backup.sh).
#   ./scripts/backup-github.sh
# Дамп кладётся несжатым текстом в один и тот же файл db/dias.sql — git хранит только
# разницу между ночами, а история коммитов = восстановление на любой прошлый день.
# Media зеркалится как есть: каждое фото хранится в git один раз.
# Доступ — deploy key только к этому репо (/root/.ssh/dias_backups_deploy).
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE="docker compose --env-file .env.prod -f docker-compose.prod.yml"
REPO_URL="${BACKUP_REPO_URL:-git@github.com:Iskanov-Nurdan/becap_Dias.git}"
REPO_DIR="${BACKUP_REPO_DIR:-/root/dias-backups-repo}"
KEY="${BACKUP_REPO_KEY:-/root/.ssh/dias_backups_deploy}"
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

log()  { echo -e "\033[1;34m[backup-github]\033[0m $(date '+%F %T') $*"; }
fail() { echo -e "\033[1;31m[backup-github] ОШИБКА:\033[0m $*" >&2; exit 1; }

[ -f "$KEY" ] || fail "нет ключа $KEY"

if [ ! -d "$REPO_DIR/.git" ]; then
    log "первый запуск — clone $REPO_URL"
    git clone -q "$REPO_URL" "$REPO_DIR"
fi
git -C "$REPO_DIR" config user.name  "DIAS server backup"
git -C "$REPO_DIR" config user.email "backup@diass.tw1.ru"
# Пустой репозиторий (ещё ни одного коммита) — pull не нужен.
if git -C "$REPO_DIR" rev-parse -q --verify HEAD >/dev/null; then
    git -C "$REPO_DIR" pull -q --ff-only
fi

# ——— БД ———
mkdir -p "$REPO_DIR/db"
$COMPOSE exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$REPO_DIR/db/dias.sql.tmp"
tail -n 5 "$REPO_DIR/db/dias.sql.tmp" | grep -q 'PostgreSQL database dump complete' \
    || { rm -f "$REPO_DIR/db/dias.sql.tmp"; fail "дамп БД неполный — на GitHub не отправлен"; }
mv "$REPO_DIR/db/dias.sql.tmp" "$REPO_DIR/db/dias.sql"

# GitHub не принимает файлы > 100 МБ — заранее предупреждаем, а не ловим отказ push.
size_mb=$(( $(stat -c %s "$REPO_DIR/db/dias.sql") / 1024 / 1024 ))
[ "$size_mb" -lt 90 ] || fail "дамп ${size_mb} МБ — близко к лимиту GitHub 100 МБ, нужна другая схема"

# ——— Media ———
media_dir="$(grep -E '^MEDIA_DIR=' .env.prod 2>/dev/null | tail -1 | cut -d= -f2 || true)"
media_dir="${media_dir:-/opt/dias/backend/media}"
if [ -d "$media_dir" ]; then
    mkdir -p "$REPO_DIR/media"
    rsync -a --delete "$media_dir/" "$REPO_DIR/media/"
fi

# ——— Отправка ———
git -C "$REPO_DIR" add -A
if git -C "$REPO_DIR" diff --cached --quiet; then
    log "изменений с прошлого раза нет — коммит не нужен"
    exit 0
fi
git -C "$REPO_DIR" commit -q -m "backup $(date '+%F %T %Z')"
git -C "$REPO_DIR" push -q origin HEAD
log "отправлено на GitHub ($(du -h "$REPO_DIR/db/dias.sql" | cut -f1) БД)"
