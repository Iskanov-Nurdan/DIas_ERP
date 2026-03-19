from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    """Жёсткий лимит для эндпоинта логина — 10 попыток в минуту с одного IP."""
    scope = 'login'


class SensitiveAnonRateThrottle(AnonRateThrottle):
    """Жёсткий лимит для анонимных запросов к чувствительным эндпоинтам."""
    scope = 'sensitive_anon'


class SensitiveUserRateThrottle(UserRateThrottle):
    """Мягкий лимит для аутентифицированных пользователей на чувствительных эндпоинтах."""
    scope = 'sensitive_user'
