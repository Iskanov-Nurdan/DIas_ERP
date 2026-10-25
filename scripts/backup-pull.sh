#!/usr/bin/env bash
# Скачать бэкапы с сервера к себе на компьютер (запускать ЛОКАЛЬНО, Linux или Git Bash).
# Копия вне сервера — на случай, если сервер/диск умрёт вместе с бэкапами на нём.
#   ./scripts/backup-pull.sh
#   SERVER=root@1.2.3.4 DEST=~/my-backups ./scripts/backup-pull.sh
# Качает только те файлы, которых ещё нет локально; локально ничего не удаляет.
set -euo pipefail

SERVER="${SERVER:-root@5.42.98.29}"
REMOTE_DIR="${REMOTE_DIR:-/root/DIas_ERP/backups}"
DEST="${DEST:-$HOME/dias-backups}"

log() { echo -e "\033[1;34m[backup-pull]\033[0m $*"; }

mkdir -p "$DEST"

missing=()
while IFS= read -r f; do
    [ -n "$f" ] && [ ! -f "$DEST/$f" ] && missing+=("$f")
done < <(ssh "$SERVER" "cd '$REMOTE_DIR' && ls -1 *.sql.gz *.tar.gz 2>/dev/null")

if [ ${#missing[@]} -eq 0 ]; then
    log "новых бэкапов нет — всё уже в $DEST"
    exit 0
fi

for f in "${missing[@]}"; do
    log "↓ $f"
    scp -q "$SERVER:$REMOTE_DIR/$f" "$DEST/$f.part"
    mv "$DEST/$f.part" "$DEST/$f"
done
log "готово: скачано ${#missing[@]}, всего в $DEST: $(ls -1 "$DEST" | wc -l)"
