from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


class Role(models.Model):
    name = models.CharField('Название', max_length=100)
    description = models.TextField('Описание', blank=True)

    class Meta:
        db_table = 'roles'
        verbose_name = 'Роль'
        verbose_name_plural = 'Роли'

    def __str__(self):
        return self.name


class RoleAccess(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='accesses')
    access_key = models.CharField('Ключ доступа', max_length=50)

    class Meta:
        db_table = 'role_access'
        unique_together = [('role', 'access_key')]
        verbose_name = 'Доступ роли'
        verbose_name_plural = 'Доступы ролей'

    def __str__(self):
        return f'{self.role.name} — {self.access_key}'


class UserAccess(models.Model):
    """Ключи вкладок UI для пользователя (единственный источник для меню, см. User.get_access_keys)."""

    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='user_accesses')
    access_key = models.CharField('Ключ доступа', max_length=50)

    class Meta:
        db_table = 'user_access'
        unique_together = [('user', 'access_key')]
        verbose_name = 'Ключ доступа (вкладка)'
        verbose_name_plural = 'Ключи доступа (вкладки)'

    def __str__(self):
        return f'{self.user_id} — {self.access_key}'


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **kwargs):
        if not email:
            raise ValueError('Email обязателен')
        email = self.normalize_email(email)
        user = self.model(email=email, **kwargs)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **kwargs):
        kwargs.setdefault('is_staff', True)
        kwargs.setdefault('is_superuser', True)
        return self.create_user(email, password, **kwargs)


class User(AbstractBaseUser, PermissionsMixin):
    name = models.CharField('Имя', max_length=255)
    email = models.EmailField('Email', unique=True)
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True, related_name='users')
    is_active = models.BooleanField('Активен', default=True)
    is_staff = models.BooleanField('Персонал', default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['name']

    class Meta:
        db_table = 'users'
        verbose_name = 'Пользователь'
        verbose_name_plural = 'Пользователи'

    def __str__(self):
        return self.email

    def get_access_keys(self):
        """
        Только UserAccess. Пустой список и не суперпользователь → нет вкладок в UI.
        Суперпользователь без строк UserAccess → полный ACCESS_KEYS (обход API).
        """
        keys = sorted(set(self.user_accesses.values_list('access_key', flat=True)))
        if keys:
            return keys
        if getattr(self, 'is_superuser', False):
            return list(getattr(settings, 'ACCESS_KEYS', []))
        return []
