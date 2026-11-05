from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from transactions.csv_import import read_transactions_csv
from transactions.models import Transaction, TransactionImport


def import_transactions(user, file, *, replace=False):
    # сохраняет операции пользователя после проверки всего файла
    if not user.is_authenticated:
        raise PermissionDenied
    rows, checksum = read_transactions_csv(file)
    return save_transactions(user, rows, checksum, replace=replace)


def save_transactions(user, rows, checksum="", *, replace=False):
    # добавляет операции или заменяет историю по галочке
    if not user.is_authenticated:
        raise PermissionDenied
    if not rows:
        raise ValidationError("Добавьте хотя бы одну операцию")
    dates = [row["occurred_at"] for row in rows]
    period_start, period_end = min(dates), max(dates)

    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        if replace:
            TransactionImport.objects.filter(user=user).delete()
        history, created = TransactionImport.objects.get_or_create(user=user, defaults={
            "row_count": 0, "period_start": period_start, "period_end": period_end,
            "file_sha256": checksum,
        })
        history.row_count += len(rows)
        history.period_start = min(history.period_start, period_start)
        history.period_end = max(history.period_end, period_end)
        if not created:
            history.file_sha256 = ""
        history.save(update_fields=["row_count", "period_start", "period_end", "file_sha256"])
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
