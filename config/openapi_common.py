"""Общие схемы для drf-spectacular (формат ошибок DIAS)."""
from rest_framework import serializers
from drf_spectacular.utils import inline_serializer


def paginated_inline(name: str, item_serializer_class):
    """Контракт StandardResultsSetPagination: items, meta, links."""
    return inline_serializer(
        name,
        fields={
            'items': serializers.ListField(child=item_serializer_class()),
            'meta': inline_serializer(
                f'{name}Meta',
                fields={
                    'total': serializers.IntegerField(),
                    'page': serializers.IntegerField(),
                    'perPage': serializers.IntegerField(),
                    'totalPages': serializers.IntegerField(),
                    'total_pages': serializers.IntegerField(),
                },
            ),
            'links': inline_serializer(
                f'{name}Links',
                fields={
                    'next': serializers.URLField(allow_null=True, required=False),
                    'previous': serializers.URLField(allow_null=True, required=False),
                },
            ),
        },
    )


DiasErrorSerializer = inline_serializer(
    name='DiasError',
    fields={
        'code': serializers.CharField(help_text='Код ошибки (см. docs/API_README.md).'),
        'error': serializers.CharField(help_text='Краткое сообщение для UI.'),
        'detail': serializers.CharField(help_text='Дублирует error для совместимости.'),
        'wait': serializers.IntegerField(
            required=False,
            allow_null=True,
            help_text='Только при 429: секунды до сброса лимита (дублирует смысл текста в detail).',
        ),
        'errors': serializers.ListField(
            child=inline_serializer(
                name='DiasValidationErrorItem',
                fields={
                    'field': serializers.CharField(),
                    'message': serializers.CharField(),
                },
            ),
            required=False,
            help_text='При validation_error: список полей и сообщений.',
        ),
    },
)
