# DIAS · production (docker compose)
# Использование: make up | make logs | make migrate | make backup
COMPOSE := docker compose --env-file .env.prod -f docker-compose.prod.yml

.PHONY: build up down restart logs ps migrate seed superuser shell dbshell collectstatic backup restore prune

build:          ## Собрать образы
	$(COMPOSE) build --pull

up:             ## Собрать и поднять весь стек в фоне
	$(COMPOSE) up -d --build
	$(COMPOSE) ps

down:           ## Остановить стек (данные в volume сохраняются)
	$(COMPOSE) down

restart:        ## Перезапустить сервисы
	$(COMPOSE) restart

logs:           ## Хвост логов всех сервисов
	$(COMPOSE) logs -f --tail=100

ps:             ## Статус и healthcheck'и
	$(COMPOSE) ps

migrate:        ## Применить миграции
	$(COMPOSE) exec backend python manage.py migrate --noinput

seed:           ## Роли + суперпользователь admin/admin (первичная инициализация)
	$(COMPOSE) exec backend python manage.py seed_roles

superuser:      ## Создать суперпользователя интерактивно
	$(COMPOSE) exec backend python manage.py createsuperuser

collectstatic:  ## Пересобрать статику
	$(COMPOSE) exec backend python manage.py collectstatic --noinput

shell:          ## Django shell
	$(COMPOSE) exec backend python manage.py shell

dbshell:        ## psql внутри контейнера БД
	$(COMPOSE) exec db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

backup:         ## Дамп БД в ./backups/dias-<дата>.sql.gz
	@mkdir -p backups
	$(COMPOSE) exec -T db sh -c 'pg_dump -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"' \
		| gzip > backups/dias-$$(date +%Y%m%d-%H%M%S).sql.gz
	@ls -lh backups | tail -1

restore:        ## Восстановить: make restore FILE=backups/dias-....sql.gz
	@test -n "$(FILE)" || (echo "Укажите FILE=backups/....sql.gz" && exit 1)
	gunzip -c $(FILE) | $(COMPOSE) exec -T db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

prune:          ## Удалить неиспользуемые образы/слои
	docker image prune -f

# ─────────── Диагностика ───────────

doctor:         ## Общая картина: статус, health, свежие ошибки, диск
	@echo "── Контейнеры ──"
	@$(COMPOSE) ps
	@echo "\n── Health бэкенда ──"
	@$(COMPOSE) exec -T backend python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health/').read().decode())" 2>&1 || echo "бэкенд не отвечает"
	@echo "\n── Ошибки за последний час ──"
	@$(COMPOSE) logs --since 1h --no-color 2>&1 | grep -Ei "error|traceback|exception|critical|status=5[0-9][0-9]" | tail -30 || echo "ошибок нет"
	@echo "\n── Диск ──"
	@df -h / | tail -1
	@docker system df

errors:         ## Только ошибки бэкенда (ERROR / Traceback) за 24 часа
	@$(COMPOSE) logs backend --since 24h --no-color 2>&1 | grep -EA 25 "\[ERROR\]|Traceback" | tail -120

logs-backend:   ## Логи Django
	$(COMPOSE) logs -f --tail=200 backend

logs-nginx:     ## Логи nginx (status=, upstream=, rid=)
	$(COMPOSE) logs -f --tail=200 nginx

logs-db:        ## Логи PostgreSQL
	$(COMPOSE) logs -f --tail=200 db

http-errors:    ## Все 4xx/5xx из access-лога nginx
	@$(COMPOSE) logs nginx --since 24h --no-color 2>&1 | grep -E "status=(4|5)[0-9][0-9]" | tail -50

trace:          ## Весь путь одного запроса: make trace RID=<X-Request-Id>
	@test -n "$(RID)" || (echo "Укажите RID=<значение заголовка X-Request-Id из ответа>" && exit 1)
	@echo "── nginx ──"
	@$(COMPOSE) logs nginx --no-color 2>&1 | grep -F "$(RID)" || echo "нет записей"
	@echo "\n── backend ──"
	@$(COMPOSE) logs backend --no-color 2>&1 | grep -FA 25 "request_id=$(RID)" || echo "нет записей"

debug-on:       ## Подробные логи (LOG_LEVEL=DEBUG) без DEBUG=True
	$(COMPOSE) run --rm -e LOG_LEVEL=DEBUG --service-ports backend || true

conf-check:     ## Проверить конфиг nginx и настройки Django
	$(COMPOSE) exec nginx nginx -t
	$(COMPOSE) exec backend python manage.py check --deploy
