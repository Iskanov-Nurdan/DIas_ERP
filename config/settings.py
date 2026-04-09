import os
from pathlib import Path

from corsheaders.defaults import default_headers

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-change-in-production')

DEBUG = os.environ.get('DEBUG', 'True').lower() == 'true'

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

INSTALLED_APPS = [
    'daphne',  # должен быть до django.contrib.staticfiles: подменяет runserver на ASGI (иначе WebSocket не работает)
    'channels',
    'jazzmin',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'django_filters',
    'corsheaders',
    'drf_spectacular',
    'apps.accounts.apps.AccountsConfig',
    'apps.materials',
    'apps.chemistry',
    'apps.recipes',
    'apps.production',
    'apps.warehouse',
    'apps.sales',
    'apps.otk',
    'apps.analytics',
    'apps.activity',
    'apps.realtime',
]

MIDDLEWARE = [
    # CORS — максимально высоко: ответ на OPTIONS до CommonMiddleware и прочих.
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'config.middleware.request_id_middleware',   # X-Request-Id после CORS (на обычных запросах)
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'config.middleware.utf8_json_content_type',  # Content-Type: application/json; charset=utf-8
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [],
    'APP_DIRS': True,
    'OPTIONS': {
        'context_processors': [
            'django.template.context_processors.debug',
            'django.template.context_processors.request',
            'django.contrib.auth.context_processors.auth',
            'django.contrib.messages.context_processors.messages',
        ],
    },
}]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# ——— Django Channels (WebSocket) ———
_redis_channel = os.environ.get('REDIS_URL') or os.environ.get('CHANNEL_LAYER_REDIS')
if _redis_channel:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {'hosts': [_redis_channel]},
        },
    }
else:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        },
    }

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
    }
}

AUTH_USER_MODEL = 'accounts.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ——— REST & JWT ———
# Контракт для фронта: page, page_size, search, ordering, полевые фильтры (filterset_fields).
# Ответ списков: items, meta (total_count, page, page_size, total_pages), links (next, previous).
_renderers = ['config.renderers.UTF8JSONRenderer']
if DEBUG:
    _renderers.append('rest_framework.renderers.BrowsableAPIRenderer')

REST_FRAMEWORK = {
    # Decimal в JSON как числа; рендерер DiasJSONEncoder убирает .0 у целых.
    'COERCE_DECIMAL_TO_STRING': False,
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_RENDERER_CLASSES': _renderers,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_PAGINATION_CLASS': 'config.pagination.StandardResultsSetPagination',
    'PAGE_SIZE': 20,
    'EXCEPTION_HANDLER': 'config.exceptions.dias_exception_handler',
    'UNICODE_JSON': True,
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '120/min',
        'user': '2000/hour',
        'login': '10/min',
        'sensitive_anon': '10/min',
        'sensitive_user': '30/min',
    },
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

from datetime import timedelta
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=24),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
}

# ——— CORS (по окружениям) ———
_raw_cors_origins = os.environ.get(
    'CORS_ALLOWED_ORIGINS',
    'http://localhost:3000,http://127.0.0.1:3000',
).split(',')
CORS_ALLOWED_ORIGINS = [o.strip() for o in _raw_cors_origins if o.strip()]
if DEBUG and not os.environ.get('CORS_ALLOWED_ORIGINS'):
    CORS_ALLOW_ALL_ORIGINS = False
    CORS_ALLOWED_ORIGINS = ['http://localhost:3000', 'http://127.0.0.1:3000']

# Кастомные заголовки: расширяем default_headers (а не подменяем целиком).
CORS_ALLOW_HEADERS = list(default_headers) + [
    'x-request-id',
    'x-correlation-id',
    'x-audit-shift-id',
    'x-shift-id',
]

# WebSocket handshake: браузер шлёт заголовок Origin страницы фронта (как при CORS), а не только Host API.
# Список ниже переопределяет источники для channels.security.websocket.OriginValidator; пусто = взять CORS_ALLOWED_ORIGINS
# (см. config/asgi.py). Переменная окружения: WEBSOCKET_ALLOWED_ORIGINS=http://app:3000,https://app.example.com
_ws_allowed_env = os.environ.get('WEBSOCKET_ALLOWED_ORIGINS', '').strip()
CHANNELS_WS_ALLOWED_ORIGINS = [o.strip() for o in _ws_allowed_env.split(',') if o.strip()]

# Явный флаг «разрешить любой Origin» для WS (только если осознанно; иначе ориентируемся на CORS_ALLOW_ALL_ORIGINS).
CHANNELS_WS_ALLOW_ALL_ORIGINS = os.environ.get('CHANNELS_WS_ALLOW_ALL_ORIGINS', '').lower() in ('1', 'true', 'yes')

# ——— OpenAPI 3 (drf-spectacular) ———
SPECTACULAR_SETTINGS = {
    'TITLE': 'DIAS API',
    'DESCRIPTION': (
        'REST API учёта производства DIAS. Машиночитаемая схема — источник истины для контрактов; '
        'человекочитаемые обзоры: `docs/API_README.md`, `docs/WEBSOCKET_API.md`, матрица UI — `docs/API_UI_MATRIX.md`.'
    ),
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
    'SCHEMA_PATH_PREFIX': r'/api',
    'TAGS': [
        {'name': 'auth', 'description': 'Вход, профиль, выход (JWT).'},
        {'name': 'accounts', 'description': 'Пользователи и роли (RBAC).'},
        {'name': 'production', 'description': 'Линии, заказы, партии, замесы, смены.'},
        {'name': 'materials', 'description': 'Сырьё, приходы, остатки, списания.'},
        {'name': 'chemistry', 'description': 'Химия: каталог, задания, остатки, запуски рецептов.'},
        {'name': 'recipes', 'description': 'Рецептуры.'},
        {'name': 'warehouse', 'description': 'Склад готовой продукции.'},
        {'name': 'sales', 'description': 'Клиенты и продажи.'},
        {'name': 'otk', 'description': 'ОТК: очередь партий.'},
        {'name': 'analytics', 'description': 'Аналитика (только HTTP, без WebSocket).'},
        {'name': 'activity', 'description': 'Журнал действий / аудит.'},
    ],
    'APPEND_COMPONENTS': {
        'securitySchemes': {
            'bearerAuth': {
                'type': 'http',
                'scheme': 'bearer',
                'bearerFormat': 'JWT',
                'description': 'Access-токен из POST /api/auth/login (поле token). Заголовок: Authorization: Bearer <token>',
            },
        },
    },
    'SECURITY': [{'bearerAuth': []}],
}

# ——— Jazzmin (админка) ———
JAZZMIN_SETTINGS = {
    'site_title': 'DIAS Admin',
    'site_header': 'DIAS',
    'site_brand': 'DIAS',
    'welcome_sign': 'Добро пожаловать в панель DIAS',
    'copyright': 'DIAS',
    'search_model': ['accounts.User'],
    'user_avatar': None,
    'topmenu_links': [],
    'show_sidebar': True,
    'navigation': [
        {'app': 'accounts', 'name': 'Пользователи и роли', 'icon': 'fas fa-users'},
        {'app': 'production', 'name': 'Линии и заказы', 'icon': 'fas fa-industry'},
        {'app': 'materials', 'name': 'Склад сырья', 'icon': 'fas fa-boxes'},
        {'app': 'chemistry', 'name': 'Химические элементы', 'icon': 'fas fa-flask'},
        {'app': 'recipes', 'name': 'Рецепты', 'icon': 'fas fa-book'},
        {'app': 'production', 'name': 'Производство', 'icon': 'fas fa-cogs'},
        {'app': 'otk', 'name': 'ОТК', 'icon': 'fas fa-clipboard-check'},
        {'app': 'warehouse', 'name': 'Склад ГП', 'icon': 'fas fa-warehouse'},
        {'app': 'sales', 'name': 'Клиенты и продажи', 'icon': 'fas fa-shopping-cart'},
        {'app': 'analytics', 'name': 'Аналитика', 'icon': 'fas fa-chart-line'},
    ],
    'order_with_respect_to': [
        'accounts', 'production', 'materials', 'chemistry', 'recipes',
        'otk', 'warehouse', 'sales', 'analytics',
    ],
    'icons': {
        'accounts': 'fas fa-users',
        'accounts.user': 'fas fa-user',
        'accounts.role': 'fas fa-user-tag',
        'accounts.roleaccess': 'fas fa-key',
        'materials': 'fas fa-boxes',
        'materials.rawmaterial': 'fas fa-cube',
        'materials.incoming': 'fas fa-truck-loading',
        'chemistry': 'fas fa-flask',
        'chemistry.chemistrycatalog': 'fas fa-vial',
        'chemistry.chemistrytask': 'fas fa-tasks',
        'chemistry.chemistrystock': 'fas fa-database',
        'recipes': 'fas fa-book',
        'recipes.recipe': 'fas fa-book-open',
        'recipes.recipecomponent': 'fas fa-list',
        'production': 'fas fa-industry',
        'production.line': 'fas fa-border-all',
        'production.linehistory': 'fas fa-history',
        'production.order': 'fas fa-clipboard-list',
        'production.productionbatch': 'fas fa-box',
        'production.reciperun': 'fas fa-play-circle',
        'production.reciperunbatch': 'fas fa-flask',
        'production.reciperunbatchcomponent': 'fas fa-balance-scale',
        'warehouse': 'fas fa-warehouse',
        'warehouse.warehousebatch': 'fas fa-pallet',
        'sales': 'fas fa-shopping-cart',
        'sales.client': 'fas fa-address-book',
        'sales.sale': 'fas fa-file-invoice',
        'sales.shipment': 'fas fa-shipping-fast',
        'otk': 'fas fa-clipboard-check',
        'otk.otkcheck': 'fas fa-check-double',
        'analytics': 'fas fa-chart-line',
    },
    'default_icon_parents': 'fas fa-chevron-circle-right',
    'default_icon_children': 'fas fa-circle',
}

JAZZMIN_UI_TWEAKS = {
    'navbar_small_text': False,
    'footer_small_text': False,
    'body_small_text': False,
    'brand_small_text': False,
    'brand_colour': 'navbar-primary',
    'accent': 'accent-primary',
    'navbar': 'navbar-dark navbar-primary',
    'no_navbar_border': False,
    'navbar_fixed': True,
    'layout_boxed': False,
    'footer_fixed': False,
    'sidebar_fixed': True,
    'sidebar': 'sidebar-dark-primary',
    'sidebar_nav_small_text': False,
    'sidebar_disable_expand': False,
    'sidebar_nav_child_indent': False,
    'sidebar_nav_compact_style': False,
    'sidebar_nav_legacy_style': False,
    'sidebar_nav_flat_style': False,
    'theme': 'default',
    'dark_mode_theme': None,
    'button_classes': {
        'primary': 'btn-primary',
        'secondary': 'btn-secondary',
        'info': 'btn-info',
        'warning': 'btn-warning',
        'danger': 'btn-danger',
        'success': 'btn-success',
    },
}

# ——— Логирование (структурированное, request id) ———
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'request_id': {
            '()': 'config.logging.RequestIdFilter',
        },
    },
    'formatters': {
        'verbose': {
            'format': '{asctime} [{levelname}] request_id={request_id} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'filters': ['request_id'],
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django.request': {'level': 'WARNING'},
    },
}

# Access keys для RBAC (совпадают с разделами из ТЗ)
ACCESS_KEYS = [
    'users', 'lines', 'materials', 'chemistry', 'recipes', 'orders',
    'production', 'otk', 'warehouse', 'clients', 'sales', 'shipments', 'analytics',
    'shifts', 'my_shift',
]

# Имена ролей из seed_roles: при создании User без role назначается «Планировщик»,
# для суперпользователя — «Админ» (чтобы вкладки UI совпадали с матрицей RoleAccess).
USERS_DEFAULT_ROLE_NAME = 'Планировщик'
USERS_SUPERUSER_ROLE_NAME = 'Админ'

# Ключи вкладок для роли «Планировщик» (как в seed_roles)
USERS_PLANNER_ACCESS_KEYS = [
    'lines', 'recipes', 'orders', 'production',
]
