"""
Рендерер JSON с явным charset=utf-8 и кириллицей без экранирования.
Устраняет мозаику (РђРґРјРёРЅРёСЃС‚СЂР°С„РѕСЂ вместо Администратор) на фронте.
"""
from rest_framework.renderers import JSONRenderer


class UTF8JSONRenderer(JSONRenderer):
    media_type = "application/json"
    charset = "utf-8"
    ensure_ascii = False  # кириллица как есть, не \u0410
