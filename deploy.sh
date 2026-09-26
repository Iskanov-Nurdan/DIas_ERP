#!/usr/bin/env bash
# Деплой бэкенда DIAS одной командой (запускать на сервере из /root/DIas_ERP):
#   ./deploy.sh            — бэкап БД → код из origin/main → сборка → запуск → проверка health
#   ./deploy.sh --no-pull  — без обновления кода (собрать то, что уже лежит в папке)
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE="docker compose --env-file .env.prod -f docker-compose.prod.yml"

log()  { echo -e "\033[1;34m[deploy]\033[0m $*"; }
fail() { echo -e "\033[1;31m[deploy] ОШИБКА:\033[0m $*" >&2; exit 1; }

# ——— Проверки окружения ———
command -v docker >/dev/null || fail "docker не установлен"
docker compose version >/dev/null 2>&1 || fail "нет плагина docker compose"

if [ ! -f .env.prod ]; then
    cp .env.prod.example .env.prod
    fail "создан .env.prod из шаблона — заполните DJANGO_SECRET_KEY и PGPASSWORD и запустите снова"
fi
grep -q 'CHANGE_ME' .env.prod && fail "в .env.prod остались значения CHANGE_ME"

# Порт контейнерного nginx из HTTP_PORT (формат «8080» или «127.0.0.1:8080»)
http_port="$(grep -E '^HTTP_PORT=' .env.prod | tail -1 | cut -d= -f2 | awk -F: '{print $NF}')"
HEALTH_URL="http://127.0.0.1:${http_port:-80}/health/"

mkdir -p frontend-dist backups
[ -f frontend-dist/index.html ] || log "frontend-dist/ пустая — залейте билд фронта (Dias_Front/deploy.sh)"

# MEDIA_DIR — путь хоста для загруженных файлов (см. MEDIA_DIR в docker-compose.prod.yml
# и location /media/ в /etc/nginx/sites-enabled/diyas — оба должны указывать сюда же).
# a+rwX — backend пишет туда под системным пользователем dias (не root), без этого
# ловит permission denied при создании подпапок.
media_dir="$(grep -E '^MEDIA_DIR=' .env.prod 2>/dev/null | tail -1 | cut -d= -f2)"
media_dir="${media_dir:-/opt/dias/backend/media}"
mkdir -p "$media_dir"
chmod -R a+rwX "$media_dir"

# ——— Бэкап БД (если контейнер БД уже работает) ———
if $COMPOSE ps --status running --services 2>/dev/null | grep -qx db; then
    backup="backups/dias-$(date +%Y%m%d-%H%M%S).sql.gz"
    log "бэкап БД → $backup"
    $COMPOSE exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip > "$backup"
    # храним последние 10 бэкапов
    ls -1t backups/dias-*.sql.gz 2>/dev/null | tail -n +11 | xargs -r rm -f
else
    log "БД ещё не запущена — бэкап пропущен (первый деплой)"
fi

# ——— Код ———
if [ "${1:-}" != "--no-pull" ]; then
    # Явно origin/main: не зависим от имени локальной ветки и её upstream.
    # .env.prod, frontend-dist/, backups/ не в git — reset их не трогает.
    log "обновление кода из origin/main"
    git fetch origin main
    git checkout -q -B main origin/main
    git reset -q --hard origin/main
fi

# ——— Сборка и запуск ———
log "сборка и запуск контейнеров"
$COMPOSE up -d --build --remove-orphans

# ——— Проверка ———
log "ожидание health-check"
for i in $(seq 1 30); do
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
        $COMPOSE ps
        log "готово: $(curl -fsS "$HEALTH_URL")"
        docker image prune -f >/dev/null
        exit 0
    fi
    sleep 3
done

$COMPOSE ps
$COMPOSE logs --tail=50 backend
fail "бэкенд не ответил на $HEALTH_URL за 90 с — логи выше"
