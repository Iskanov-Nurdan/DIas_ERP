"""
Middleware: явно указываем charset=utf-8 для JSON-ответов API,
чтобы клиент (фронт) корректно декодировал кириллицу (нет мозаики вместо «Администратор»).
"""


def utf8_json_content_type(get_response):
    def middleware(request):
        response = get_response(request)
        content_type = (response.get("Content-Type") or "").lower()
        if "application/json" in content_type:
            # Всегда выставляем charset=utf-8 для JSON, иначе браузер/фронт может интерпретировать как Latin-1
            response["Content-Type"] = "application/json; charset=utf-8"
        return response

    return middleware
