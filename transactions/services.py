from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from transactions.csv_import import read_transactions_csv
from transactions.models import Transaction, TransactionImport


def import_transactions(user, file):
    # заменяет историю пользователя после проверки всего файла
    if not user.is_authenticated:
        raise PermissionDenied
    rows, checksum = read_transactions_csv(file)
    dates = [row["occurred_at"] for row in rows]

    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        TransactionImport.objects.filter(user=user).delete()
        history = TransactionImport.objects.create(
            user=user,
            row_count=len(rows),
            period_start=min(dates),
            period_end=max(dates),
            file_sha256=checksum,
        )
        Transaction.objects.bulk_create(
            [Transaction(history=history, **row) for row in rows], batch_size=1000
        )
    return history


def get_user_transactions(user, *, before=None):
    # возвращает только свои операции с нужным ограничением по времени
    if not user.is_authenticated:
        raise PermissionDenied
    rows = Transaction.objects.filter(history__user=user)
    if before is not None:
        if timezone.is_naive(before):
            raise ValidationError("Момент перевода должен содержать часовой пояс")
        rows = rows.filter(occurred_at__lt=before)
    return list(
        rows.order_by("-occurred_at", "-id").values(
            "occurred_at", "amount_kopecks", "currency", "direction", "recipient"
        )
    )


def delete_user_transactions(user):
    # удаляет только свою загруженную историю
    if not user.is_authenticated:
        raise PermissionDenied
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        TransactionImport.objects.filter(user=user).delete()
