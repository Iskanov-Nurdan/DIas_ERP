"""
Рендерер JSON с явным charset=utf-8 и кириллицей без экранирования.
Устраняет мозаику (кракозябры вместо кириллицы) на фронте.

Decimal: целые отдаём как int (4, а не 4.0), дробные — как float без лишней «строковости» в сериализаторе.
"""
import decimal
import json

from rest_framework.renderers import JSONRenderer
from rest_framework.utils.encoders import JSONEncoder


class DiasJSONEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            if obj.is_nan() or obj.is_infinite():
                return None
            try:
                if obj == obj.to_integral_value() and abs(obj) < 10**15:
                    return int(obj)
            except (ValueError, OverflowError):
                pass
            return float(obj)
        return super().default(obj)


class UTF8JSONRenderer(JSONRenderer):
    media_type = "application/json"
    charset = "utf-8"
    ensure_ascii = False  # кириллица как есть, не \u0410
    encoder_class = DiasJSONEncoder
