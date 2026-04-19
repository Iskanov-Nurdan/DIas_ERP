# DIAS Backend

Бэкенд системы учёта производства пластика (Django REST API).

## Стек

- Python 3.10+
- Django 4.2, Django REST Framework
- JWT (djangorestframework-simplejwt)
- drf-yasg (Swagger / OpenAPI)
- django-jazzmin (админка)
- SQLite (по умолчанию) или PostgreSQL

## Установка

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

## База и миграции

```bash
python manage.py migrate
python manage.py seed_roles   # роли + суперпользователь admin / admin
```

## Запуск

```bash
python manage.py runserver
```

- API: http://127.0.0.1:8000/api/
- Swagger UI: http://127.0.0.1:8000/api/docs/
- OpenAPI JSON: http://127.0.0.1:8000/api/openapi.json
- Админка: http://127.0.0.1:8000/admin/

## Документация для фронтенда

- **Markdown:** [docs/API_FRONTEND.md](docs/API_FRONTEND.md) — полное описание API, форматы ответов, ошибок, RBAC, примеры.
- **Markdown (коротко, по вкладкам):** [docs/FRONTEND_TABS_BACKLOGIC.md](docs/FRONTEND_TABS_BACKLOGIC.md) — бизнес-логика и работа фронта по каждому разделу меню.
- **HTML (для PDF):** [docs/API_FRONTEND.html](docs/API_FRONTEND.html) — та же документация в виде страницы; откройте в браузере и сохраните в PDF через «Печать → Сохранить как PDF».

## Авторизация

- `POST /api/auth/login` — тело `{ "name": "admin", "password": "admin" }` → `{ "token", "user" }`
- Заголовок: `Authorization: Bearer <token>`
- `GET /api/me` — текущий пользователь и список доступов `accesses[]`

## Окружение

- `DEBUG` — True/False
- `DJANGO_SECRET_KEY`
- `ALLOWED_HOSTS` — через запятую
- `CORS_ALLOWED_ORIGINS` — через запятую
- `FRONTEND_PORTS` — локальные порты фронта для CORS/WS, по умолчанию `3000,5173`
- PostgreSQL: `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGHOST`, `PGPORT`
