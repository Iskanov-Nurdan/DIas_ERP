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
