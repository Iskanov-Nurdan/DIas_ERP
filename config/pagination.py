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
                'total_count': self.page.paginator.count,
                'page': self.page.number,
                'page_size': self.get_page_size(self.request),
                'total_pages': self.page.paginator.num_pages,
            },
            'links': {
                'next': self.get_next_link(),
                'previous': self.get_previous_link(),
            },
        })


class CursorResultsSetPagination(CursorPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100
    ordering = '-id'

    def get_paginated_response(self, data):
        return Response({
            'items': data,
            'meta': {
                'page_size': self.get_page_size(self.request),
            },
            'links': {
                'next': self.get_next_link(),
                'previous': self.get_previous_link(),
            },
        })
