import logging

from config.middleware import get_current_request_id


class RequestIdFilter(logging.Filter):
    """Добавляет request_id из thread-local в каждую запись лога."""

    def filter(self, record):
        record.request_id = get_current_request_id()
        return True
