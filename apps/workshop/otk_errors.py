from rest_framework.exceptions import APIException


class OtkProfileBlankMismatch(APIException):
    status_code = 400

    def __init__(self, *, profile_id: int, expected_blank_id: int):
        super().__init__(
            detail={
                'error': 'Профиль не привязан к этой заготовке',
                'profile_id': profile_id,
                'expected_blank_id': expected_blank_id,
            }
        )


class OtkPoolInsufficient(APIException):
    status_code = 400

    def __init__(self, *, blank_id: int, remaining_kg, required_kg):
        super().__init__(
            detail={
                'error': 'Недостаточно кг в пуле ОТК',
                'blank_id': blank_id,
                'remaining_kg': str(remaining_kg),
                'required_kg': str(required_kg),
            }
        )


class OtkProfileBlankMissing(APIException):
    status_code = 400

    def __init__(self, *, profile_id: int):
        super().__init__(
            detail={
                'error': 'У профиля не задана заготовка',
                'profile_id': profile_id,
            }
        )
