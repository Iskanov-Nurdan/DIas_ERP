import decimal

from rest_framework import serializers
from rest_framework.fields import localize_input
from rest_framework.settings import api_settings

from .decimal_format import format_decimal_plain


class CleanDecimalField(serializers.DecimalField):
    """
    Как DecimalField, но при coerce_to_string строка без хвостовых нулей
    (например «10», а не «10.0000» при decimal_places=4).
    """

    def to_representation(self, value):
        coerce_to_string = getattr(self, 'coerce_to_string', api_settings.COERCE_DECIMAL_TO_STRING)
        if value is None:
            if coerce_to_string:
                return ''
            return None
        if not isinstance(value, decimal.Decimal):
            value = decimal.Decimal(str(value).strip())
        quantized = self.quantize(value)
        if self.normalize_output:
            quantized = quantized.normalize()
        if not coerce_to_string:
            return quantized
        if self.localize:
            return localize_input(quantized)
        plain = format_decimal_plain(quantized)
        return plain if plain is not None else ''
