from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Role, RoleAccess

User = get_user_model()


class RoleAccessSerializer(serializers.ModelSerializer):
    class Meta:
        model = RoleAccess
        fields = ('id', 'access_key')


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ('id', 'name')

    def create(self, validated_data):
        role = Role.objects.create(**validated_data)
        return role

    def update(self, instance, validated_data):
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
        qs = User.objects.filter(name=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('Пользователь с таким именем уже существует')
        return value

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        # Генерируем email из имени
        name_slug = validated_data['name'].lower().replace(' ', '_')
        validated_data['email'] = f"{name_slug}@local.dias"
        user = User.objects.create(**validated_data)
        if password:
            user.set_password(password)
            user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        for k, v in validated_data.items():
            setattr(instance, k, v)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class UserAccessSerializer(serializers.Serializer):
    access_keys = serializers.ListField(child=serializers.CharField())

    def validate_access_keys(self, value):
        from django.conf import settings
        allowed = getattr(settings, 'ACCESS_KEYS', [])
        if allowed and any(k not in allowed for k in value):
            invalid = [k for k in value if k not in allowed]
            raise serializers.ValidationError(f'Недопустимые ключи доступа: {invalid}')
        return value

    def update(self, instance, validated_data):
        from .models import RoleAccess
        keys = validated_data['access_keys']
        if instance.role_id:
            instance.role.accesses.all().delete()
            for key in keys:
                RoleAccess.objects.create(role=instance.role, access_key=key)
        return instance


class MeSerializer(serializers.ModelSerializer):
    role_name = serializers.CharField(source='role.name', read_only=True)
    accesses = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('id', 'name', 'email', 'role', 'role_name', 'accesses')

    def get_accesses(self, obj):
        return obj.get_access_keys()
