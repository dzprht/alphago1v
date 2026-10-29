import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone

from checks.forms import (
    TRAINING_CONSENT_VERSION,
    CheckFeedbackForm,
    TrainingConsentForm,
)
from checks.models import Check, CheckFeedback, TrainingConsent


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
    checks = get_user_checks(user).select_related("feedback", "training_consent")
    return get_object_or_404(checks, pk=check_id)


def save_check_feedback(user, check_id, data):
    # сохраняет или исправляет отзыв о своей проверке
    check = get_user_check(user, check_id)
    if check.status != Check.Status.COMPLETED:
        raise ValidationError("Отзыв можно оставить только к готовому результату")
    form = CheckFeedbackForm(data=data)
    if form.is_valid():
        CheckFeedback.objects.update_or_create(check_record=check, defaults=form.cleaned_data)
    return form


def get_check_feedback(user, check_id):
    # возвращает отзыв только к своей проверке
    check = get_user_check(user, check_id)
    return getattr(check, "feedback", None)


def save_training_consent(user, check_id, data):
    # сохраняет отдельное решение об использовании данных проверки
    check = get_user_check(user, check_id)
    if check.status != Check.Status.COMPLETED:
        raise ValidationError("Согласие можно изменить только для готового результата")
    form = TrainingConsentForm(data=data)
    if form.is_valid():
        TrainingConsent.objects.update_or_create(
            check_record=check,
            defaults={
                "granted": form.cleaned_data["granted"],
                "terms_version": TRAINING_CONSENT_VERSION,
            },
        )
    return form


def consent_is_current(consent):
    # проверяет согласие на текущие условия
    return (
        consent is not None
        and consent.granted
        and consent.terms_version == TRAINING_CONSENT_VERSION
    )


def has_training_consent(user, check_id):
    # проверяет согласие на текущие условия для своей проверки
    check = get_user_check(user, check_id)
    return consent_is_current(getattr(check, "training_consent", None))
