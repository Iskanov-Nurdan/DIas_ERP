from rest_framework import status
from rest_framework.exceptions import APIException


class WorkshopConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = 'Конфликт операции учёта цеха.'
    default_code = 'workshop_conflict'
