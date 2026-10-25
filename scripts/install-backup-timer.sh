#!/usr/bin/env bash
# Установить на компьютере разработчика (Linux с systemd) ежедневное автоскачивание
# бэкапов с сервера в ~/dias-backups — после переустановки системы достаточно:
#   ./scripts/install-backup-timer.sh
# Повторный запуск безопасен: перезапишет таймер тем же содержимым.
# Нужен SSH-доступ к серверу без пароля (ключ ~/.ssh добавлен на root@5.42.98.29).
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
units="$HOME/.config/systemd/user"

log()  { echo -e "\033[1;34m[backup-timer]\033[0m $*"; }
fail() { echo -e "\033[1;31m[backup-timer] ОШИБКА:\033[0m $*" >&2; exit 1; }

command -v systemctl >/dev/null || fail "нет systemd — на этой системе таймер не поставить, запускайте scripts/backup-pull.sh вручную"

mkdir -p "$units"

cat > "$units/dias-backup-pull.service" <<EOF
[Unit]
Description=DIAS: скачать бэкапы БД с сервера в ~/dias-backups
After=network-online.target

[Service]
Type=oneshot
ExecStart="$script_dir/backup-pull.sh"
EOF

# Сервер живёт по UTC и делает ночной бэкап в 03:00 UTC — качаем через час после.
cat > "$units/dias-backup-pull.timer" <<'EOF'
[Unit]
Description=DIAS: ежедневное скачивание бэкапов (04:00 UTC — через час после ночного бэкапа на сервере)

[Timer]
OnCalendar=*-*-* 04:00 UTC
# Если компьютер был выключен в это время — скачает при следующем включении.
Persistent=true
RandomizedDelaySec=5min

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now dias-backup-pull.timer >/dev/null

log "таймер установлен, проверочное скачивание:"
systemctl --user start dias-backup-pull.service \
    || fail "скачивание не удалось — см. journalctl --user -u dias-backup-pull.service"
journalctl --user -u dias-backup-pull.service -n 3 --no-pager -o cat
systemctl --user list-timers dias-backup-pull.timer --no-pager | head -2
