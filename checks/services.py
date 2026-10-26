import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone

from checks.models import Check


def create_check(user, kind, input_data):
    # готовит проверку и сохраняет ее после входа
    if not isinstance(input_data, dict):
        raise ValidationError("Данные проверки должны быть словарем")

    owner = user if user.is_authenticated else None
    check = Check(user=owner, kind=kind, input_data=input_data)
    check.full_clean(exclude=["user"] if owner is None else None)
    if owner is not None:
        check.save()
    else:
        check.created_at = timezone.now()
    return check


def finish_check(
    check,
    *,
    result=None,
    error_message="",
    analyzer_version="",
    extracted_text="",
    context_snapshot=None,
):
    # завершает проверку результатом или ошибкой
    if check.status != Check.Status.PENDING:
        raise ValidationError("Проверка уже завершена")
    texts = (error_message, analyzer_version, extracted_text)
    if not all(isinstance(value, str) for value in texts):
        raise ValidationError("Описание ошибки, версия и извлеченный текст должны быть строками")

    error_message = error_message.strip()
    analyzer_version = analyzer_version.strip()
    if context_snapshot is None:
        context_snapshot = {}
    if not isinstance(context_snapshot, dict):
        raise ValidationError("Контекст проверки должен быть словарем")

    if check.user_id is None:
        if check.pk is not None or context_snapshot:
            raise ValidationError("У гостевой проверки не должно быть записи или личной истории")
    elif check.pk is None:
        raise ValidationError("Сначала нужно создать проверку")

    if result is None:
        if not error_message:
            raise ValidationError("Нужен результат или описание ошибки")
        status = Check.Status.FAILED
    else:
        if error_message:
            raise ValidationError("Нельзя одновременно записать результат и ошибку")
        if not isinstance(result, dict) or not result:
            raise ValidationError("Результат должен быть непустым словарем")
        if not analyzer_version:
            raise ValidationError("Укажите версию анализатора")
        status = Check.Status.COMPLETED

    values = {
        "status": status,
        "result": result,
        "error_message": error_message,
        "analyzer_version": analyzer_version,
        "extracted_text": extracted_text,
        "context_snapshot": context_snapshot,
    }
    try:
        values = json.loads(json.dumps(values, allow_nan=False))
    except (TypeError, ValueError):
        raise ValidationError("Результат и контекст должны содержать только JSON данные") from None

    candidate = Check(
        user_id=check.user_id,
        kind=check.kind,
        input_data=check.input_data,
        **values,
    )
    candidate.full_clean(exclude=["user"] if check.user_id is None else None)

    if check.user_id is not None:
        updated = Check.objects.filter(
            pk=check.pk, user_id=check.user_id, status=Check.Status.PENDING
        ).update(**values)
        if updated != 1:
            raise ValidationError("Проверка уже завершена или удалена")

    for name, value in values.items():
        setattr(check, name, value)
    return check


def get_user_checks(user):
    # возвращает проверки пользователя
    if not user.is_authenticated:
        raise PermissionDenied
    return Check.objects.filter(user=user).order_by("-created_at", "-id")


def get_user_check(user, check_id):
    # возвращает свою проверку по номеру
    return get_object_or_404(get_user_checks(user), pk=check_id)
