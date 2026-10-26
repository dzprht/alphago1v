from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from threading import Barrier
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import connection

from accounts.models import User
from checks.models import Check
from checks.services import create_check, finish_check
from transactions import csv_import, services
from transactions.csv_import import read_transactions_csv
from transactions.models import Transaction, TransactionImport
from transactions.services import (
    delete_user_transactions,
    get_user_transactions,
    import_transactions,
)


HEADER = "occurred_at;amount;direction;recipient\n"
ROW = "2026-10-20T14:30:00+03:00;1500.50;outgoing;shop_1\n"


def test_csv_reads_exact_amounts_dates_and_duplicate_rows():
    # проверяет разбор сумм дат и совпадающих операций
    content = ("\ufeff" + HEADER + ROW + ROW + "2026-10-21T09:00:00Z;0,01;incoming;\n").encode()
    rows, checksum = read_transactions_csv(ContentFile(content))

    assert len(rows) == 3
    assert rows[0] == rows[1]
    assert rows[0]["occurred_at"] == datetime(2026, 10, 20, 11, 30, tzinfo=timezone.utc)
    assert rows[0]["amount_kopecks"] == 150050
    assert rows[2]["amount_kopecks"] == 1
    assert rows[2]["recipient"] == ""
    assert checksum == sha256(content).hexdigest()


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (b"wrong;header\n", "Строка 1 поле заголовок"),
        (HEADER.encode(), "хотя бы одну операцию"),
        ((HEADER + ROW.replace("+03:00", "")).encode(), "Строка 2 поле occurred_at"),
        ((HEADER + ROW.replace("1500.50", "NaN")).encode(), "Строка 2 поле amount"),
        ((HEADER + ROW.replace("1500.50", "0")).encode(), "Строка 2 поле amount"),
        ((HEADER + ROW.replace("1500.50", "1.001")).encode(), "Строка 2 поле amount"),
        ((HEADER + ROW.replace("outgoing", "unknown")).encode(), "Строка 2 поле direction"),
        ((HEADER + ROW.replace("shop_1", "x" * 101)).encode(), "Строка 2 поле recipient"),
        ((HEADER + "2026-10-20;1;outgoing\n").encode(), "Строка 2 поле csv"),
        ((HEADER + '"unfinished').encode(), "Строка 2 поле csv"),
        (b"\xff", "UTF-8"),
    ],
)
def test_csv_reports_invalid_field(content, error):
    # проверяет понятные ошибки файла и отдельных полей
    with pytest.raises(ValidationError, match=error):
        read_transactions_csv(ContentFile(content))


@pytest.mark.parametrize("limit", ["bytes", "rows"])
def test_csv_limits_are_checked(monkeypatch, limit):
    # проверяет ограничения размера и числа операций
    if limit == "bytes":
        monkeypatch.setattr(csv_import, "MAX_CSV_BYTES", 20)
    else:
        monkeypatch.setattr(csv_import, "MAX_CSV_ROWS", 1)
    with pytest.raises(ValidationError, match="поле file|Поле file"):
        read_transactions_csv(ContentFile((HEADER + ROW + ROW).encode()))


@pytest.mark.django_db
def test_import_replaces_only_own_history_and_keeps_check_snapshot():
    # проверяет замену истории без изменения чужих данных и старого результата
    user = User.objects.create_user(username="anna")
    other_user = User.objects.create_user(username="ivan")
    original = import_transactions(user, ContentFile((HEADER + ROW).encode()))
    other = import_transactions(other_user, ContentFile((HEADER + ROW).encode()))
    check = create_check(user, Check.Kind.TRANSFER, {"amount_kopecks": 100})
    finish_check(
        check,
        result={"test": True},
        analyzer_version="test-v1",
        context_snapshot={"count": 1},
    )

    content = (HEADER + ROW + ROW).encode()
    imported = import_transactions(user, ContentFile(content))
    repeated = import_transactions(user, ContentFile(content))

    assert not TransactionImport.objects.filter(pk__in=[original.pk, imported.pk]).exists()
    assert TransactionImport.objects.count() == 2
    assert len(get_user_transactions(user)) == 2
    assert repeated.row_count == 2
    assert repeated.file_sha256 == sha256(content).hexdigest()
    assert repeated.period_start == repeated.period_end
    assert Transaction.objects.filter(history=other).count() == 1
    check.refresh_from_db()
    assert check.result == {"test": True}
    assert check.context_snapshot == {"count": 1}

    delete_user_transactions(user)
    delete_user_transactions(user)
    assert get_user_transactions(user) == []
    assert TransactionImport.objects.filter(pk=other.pk).exists()
    assert len(get_user_transactions(other_user)) == 1
    check.refresh_from_db()
    assert check.context_snapshot == {"count": 1}


@pytest.mark.django_db
@pytest.mark.parametrize("failure", ["invalid_csv", "write_error"])
def test_failed_import_preserves_old_history(monkeypatch, failure):
    # проверяет сохранность истории при ошибке файла или записи
    user = User.objects.create_user(username="anna")
    old = import_transactions(user, ContentFile((HEADER + ROW).encode()))
    old_rows = get_user_transactions(user)
    content = HEADER + ROW + ROW.replace("shop_1", "shop_2")
    if failure == "invalid_csv":
        content += ROW.replace("1500.50", "-10")
        expected_error = ValidationError
    else:
        monkeypatch.setattr(
            Transaction.objects,
            "bulk_create",
            Mock(side_effect=RuntimeError("write failed")),
        )
        expected_error = RuntimeError

    with pytest.raises(expected_error):
        import_transactions(user, ContentFile(content.encode()))

    assert TransactionImport.objects.get(user=user).pk == old.pk
    assert get_user_transactions(user) == old_rows


@pytest.mark.django_db
def test_guest_cannot_import_read_or_delete_transactions():
    # проверяет запрет доступа к операциям без входа
    guest = AnonymousUser()
    with pytest.raises(PermissionDenied):
        import_transactions(guest, ContentFile((HEADER + ROW).encode()))
    with pytest.raises(PermissionDenied):
        get_user_transactions(guest)
    with pytest.raises(PermissionDenied):
        delete_user_transactions(guest)
    assert TransactionImport.objects.count() == 0


@pytest.mark.django_db
def test_history_cutoff_uses_operation_time_and_leaves_loaded_snapshot():
    # проверяет отсечение операций по времени самого перевода
    user = User.objects.create_user(username="anna")
    content = (
        HEADER + ROW
        + ROW.replace("14:30:00", "15:00:00")
        + ROW.replace("14:30:00", "16:00:00")
    )
    import_transactions(user, ContentFile(content.encode()))
    cutoff = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)

    rows = get_user_transactions(user, before=cutoff)

    assert len(rows) == 1
    assert rows[0]["occurred_at"] < cutoff
    with pytest.raises(ValidationError):
        get_user_transactions(user, before=cutoff.replace(tzinfo=None))
    delete_user_transactions(user)
    assert len(rows) == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_imports_do_not_mix_histories(monkeypatch):
    # проверяет полную замену при одновременных загрузках
    user = User.objects.create_user(username="anna")
    barrier = Barrier(2)
    original_reader = read_transactions_csv

    def read_together(file):
        # ждет пока оба файла будут разобраны
        parsed = original_reader(file)
        barrier.wait(timeout=10)
        return parsed

    def import_in_thread(recipient):
        # загружает историю в отдельном соединении
        try:
            owner = User.objects.get(pk=user.pk)
            content = (HEADER + ROW.replace("shop_1", recipient) * 2).encode()
            import_transactions(owner, ContentFile(content))
        finally:
            connection.close()

    monkeypatch.setattr(services, "read_transactions_csv", read_together)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(import_in_thread, ["shop_a", "shop_b"]))

    history = TransactionImport.objects.get(user=user)
    rows = get_user_transactions(user)
    assert history.row_count == len(rows) == 2
    assert {row["recipient"] for row in rows} in [{"shop_a"}, {"shop_b"}]
