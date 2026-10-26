from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404

from checks.models import Check


def create_check(user, kind, input_data):
    # создает проверку для пользователя
    if not user.is_authenticated:
        raise PermissionDenied
    if not isinstance(input_data, dict):
        raise ValidationError("Данные проверки должны быть словарем")

    check = Check(user=user, kind=kind, input_data=input_data)
    check.full_clean()
    check.save()
    return check


def get_user_checks(user):
    # возвращает проверки пользователя
    if not user.is_authenticated:
        raise PermissionDenied
    return Check.objects.filter(user=user).order_by("-created_at", "-id")


def get_user_check(user, check_id):
    # возвращает свою проверку по номеру
    return get_object_or_404(get_user_checks(user), pk=check_id)
