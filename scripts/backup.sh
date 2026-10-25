#!/usr/bin/env bash
# Бэкап БД + загруженных файлов (media) на сервере. Запускать из /root/DIas_ERP:
#   ./scripts/backup.sh     — вручную, из deploy.sh перед обновлением и из cron каждую ночь
# Результат: backups/dias-<дата>.sql.gz и backups/media-<дата>.tar.gz.
# Бэкап проверяется (архив не битый, дамп дописан до конца) — иначе выход с ошибкой,
# и deploy.sh дальше не пойдёт.
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE="docker compose --env-file .env.prod -f docker-compose.prod.yml"
KEEP_DB="${KEEP_DB:-30}"        # сколько последних дампов БД хранить
KEEP_MEDIA="${KEEP_MEDIA:-14}"  # и архивов media

log()  { echo -e "\033[1;34m[backup]\033[0m $(date '+%F %T') $*"; }
fail() { echo -e "\033[1;31m[backup] ОШИБКА:\033[0m $*" >&2; exit 1; }

mkdir -p backups
stamp="$(date +%Y%m%d-%H%M%S)"

# ——— БД ———
db_file="backups/dias-$stamp.sql.gz"
log "дамп БД → $db_file"
$COMPOSE exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip > "$db_file.tmp"

# pg_dump в конце plain-дампа пишет эту строку; нет её — дамп оборван.
gzip -t "$db_file.tmp" || { rm -f "$db_file.tmp"; fail "архив $db_file битый"; }
gunzip -c "$db_file.tmp" | tail -n 5 | grep -q 'PostgreSQL database dump complete' \
    || { rm -f "$db_file.tmp"; fail "дамп БД неполный — pg_dump оборвался"; }
mv "$db_file.tmp" "$db_file"
log "БД ок ($(du -h "$db_file" | cut -f1))"

# ——— Media (фотоотчёты смен и т.п.) ———
media_dir="$(grep -E '^MEDIA_DIR=' .env.prod 2>/dev/null | tail -1 | cut -d= -f2 || true)"
media_dir="${media_dir:-/opt/dias/backend/media}"
if [ -d "$media_dir" ]; then
    media_file="backups/media-$stamp.tar.gz"
    tar -czf "$media_file" -C "$(dirname "$media_dir")" "$(basename "$media_dir")"
    log "media ок → $media_file ($(du -h "$media_file" | cut -f1))"
else
    log "папки media ($media_dir) нет — пропущено"
fi

# ——— Ротация ———
# Только авто-бэкапы dias-<цифры>/media-<цифры>; ручные (dias-before-*.sql.gz) не трогаем.
ls -1t backups/dias-2*.sql.gz 2>/dev/null | tail -n +$((KEEP_DB + 1)) | xargs -r rm -f
ls -1t backups/media-2*.tar.gz 2>/dev/null | tail -n +$((KEEP_MEDIA + 1)) | xargs -r rm -f
