import logging
import uuid


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = getattr(record, 'request_id', None) or str(uuid.uuid4())[:8]
        return True
