from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Role, RoleAccess, UserAccess
from .system_constants import SYSTEM_ADMIN_ROLE_NAME, SYSTEM_ADMIN_USERNAME

User = get_user_model()


class RoleAccessSerializer(serializers.ModelSerializer):
    class Meta:
        model = RoleAccess
        fields = ('id', 'access_key')


class RoleSerializer(serializers.ModelSerializer):
    is_system = serializers.BooleanField(read_only=True)

    class Meta:
        model = Role
        fields = ('id', 'name', 'is_system')

    def validate_name(self, value):
        reserved = (value or '').strip() == SYSTEM_ADMIN_ROLE_NAME
        if reserved and not (self.instance and getattr(self.instance, 'is_system', False)):
            raise serializers.ValidationError('Название зарезервировано за системной ролью.')
        return value

    def create(self, validated_data):
        role = Role.objects.create(**validated_data)
        return role

    def update(self, instance, validated_data):
        if getattr(instance, 'is_system', False):
            raise serializers.ValidationError('Системная роль недоступна для изменения.')
        for k, v in validated_data.items():
            setattr(instance, k, v)
        instance.save()
        return instance


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    accesses = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('id', 'name', 'role', 'password', 'accesses')
        extra_kwargs = {
            'email': {'required': False},
        }

    def get_accesses(self, obj):
        return obj.get_access_keys()

    def validate_name(self, value):
        if (value or '').strip() == SYSTEM_ADMIN_USERNAME and not (
            self.instance and getattr(self.instance, 'is_system', False)
        ):
            raise serializers.ValidationError('Имя зарезервировано для системного пользователя.')
        qs = User.objects.filter(name=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('Пользователь с таким именем уже существует')
        return value

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        name_slug = validated_data['name'].lower().replace(' ', '_')
        validated_data['email'] = f"{name_slug}@local.dias"
        user = User.objects.create(**validated_data)
        if password:
            user.set_password(password)
            user.save()
        return user

    def update(self, instance, validated_data):
        if getattr(instance, 'is_system', False):
            raise serializers.ValidationError('Системный пользователь недоступен для изменения.')
        password = validated_data.pop('password', None)
        for k, v in validated_data.items():
            setattr(instance, k, v)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


def _validate_access_keys_list(value):
    allowed = getattr(settings, 'ACCESS_KEYS', [])
    if allowed and any(k not in allowed for k in value):
        invalid = [k for k in value if k not in allowed]
        raise serializers.ValidationError(f'Недопустимые ключи доступа: {invalid}')
    return value


class UserAccessPatchSerializer(serializers.Serializer):
    """PATCH users/:id/access/ — полная замена UserAccess для пользователя."""

    access_keys = serializers.ListField(child=serializers.CharField())

    def validate(self, attrs):
        if self.instance is not None and getattr(self.instance, 'is_system', False):
            raise serializers.ValidationError('Системному пользователю нельзя менять доступы через API.')
        return attrs

    def validate_access_keys(self, value):
        return _validate_access_keys_list(value)

    def update(self, instance, validated_data):
        keys = sorted(set(validated_data['access_keys']))
        instance.user_accesses.all().delete()
        for k in keys:
            UserAccess.objects.create(user=instance, access_key=k)
        return instance


class MeSerializer(serializers.ModelSerializer):
    role_name = serializers.CharField(source='role.name', read_only=True)
    accesses = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('id', 'name', 'email', 'role', 'role_name', 'accesses')

    def get_accesses(self, obj):
        return obj.get_access_keys()
