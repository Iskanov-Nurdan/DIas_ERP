from rest_framework.pagination import PageNumberPagination, CursorPagination
from rest_framework.response import Response


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response({
            'items': data,
            'meta': {
                'total': self.page.paginator.count,
                'page': self.page.number,
                'perPage': self.get_page_size(self.request),
                'totalPages': self.page.paginator.num_pages,
            },
            'links': {
                'next': self.get_next_link(),
                'previous': self.get_previous_link(),
            },
        })

    def get_paginated_response_schema(self, schema):
        return {
            'type': 'object',
            'properties': {
                'items': schema,
                'meta': {
                    'type': 'object',
                    'properties': {
                        'total': {'type': 'integer'},
                        'page': {'type': 'integer'},
                        'perPage': {'type': 'integer'},
                        'totalPages': {'type': 'integer'},
                    },
                },
                'links': {
                    'type': 'object',
                    'properties': {
                        'next': {'type': 'string', 'nullable': True},
                        'previous': {'type': 'string', 'nullable': True},
                    },
                },
            },
        }


class CursorResultsSetPagination(CursorPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100
    ordering = '-id'

    def get_paginated_response(self, data):
        return Response({
            'items': data,
            'meta': {
                'perPage': self.get_page_size(self.request),
            },
            'links': {
                'next': self.get_next_link(),
                'previous': self.get_previous_link(),
            },
        })
