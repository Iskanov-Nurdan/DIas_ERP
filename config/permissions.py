from rest_framework.permissions import BasePermission


class HasAccessKey(BasePermission):
    """
    RBAC: доступ к ресурсу по access_key (users, lines, materials, ...).
    Передаётся как access_key в конструктор или через required_access_key в view.
    """
    def __init__(self, access_key=None):
        self.access_key = access_key

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        access_key = getattr(view, 'required_access_key', None) or self.access_key
        if not access_key:
            return True
        return access_key in request.user.get_access_keys()

    def __call__(self, access_key):
        return HasAccessKey(access_key=access_key)


class CanAccessShiftComplaints(BasePermission):
    """
    Жалобы по сменам: авторизован и есть ключ my_shift или shifts (или суперпользователь).
    Полная лента — при ключе shifts; иначе только свои жалобы и где упомянут.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if getattr(request.user, 'is_superuser', False):
            return True
        keys = request.user.get_access_keys()
        return 'my_shift' in keys or 'shifts' in keys


class IsAdminOrHasProductionOrOtk(BasePermission):
    """Список/деталка партий — otk или production; создание/изменение — production."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if getattr(request.user, 'is_superuser', False):
            return True
        keys = request.user.get_access_keys()
        action = getattr(view, 'action', None)
        if action in ('create', 'update', 'partial_update', 'destroy'):
            return 'production' in keys
        return 'otk' in keys or 'production' in keys


class IsAdminOrHasAccess(BasePermission):
    """Суперпользователь или наличие access_key."""
    def __init__(self, access_key=None):
        self.access_key = access_key

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if getattr(request.user, 'is_superuser', False):
            return True
        access_key = getattr(view, 'required_access_key', None) or self.access_key
        return access_key in request.user.get_access_keys()
