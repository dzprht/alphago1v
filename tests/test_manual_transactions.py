from datetime import datetime, timezone

import pytest
from django.core.files.base import ContentFile
from django.test import Client
from django.urls import reverse

from accounts.models import User
from transactions.csv_import import read_transactions_csv
from transactions.forms import MAX_MANUAL_ROWS, ManualTransactionFormSet
from transactions.models import TransactionImport
from transactions.services import get_user_transactions, import_transactions


HEADER = "occurred_at;amount;direction;recipient\n"
ROW = "2026-10-20T14:30:00+03:00;1500.50;outgoing;shop_1\n"


def manual_data(**changes):
    # готовит одну строку ручной истории
    data = {"action": "manual", "rows-TOTAL_FORMS": "1", "rows-INITIAL_FORMS": "0",
            "rows-0-occurred_at": "2026-10-20T14:30", "rows-0-amount": "1500,50",
            "rows-0-direction": "outgoing", "rows-0-recipient": "shop_1"}
    data.update(changes)
    return data


def test_manual_form_uses_same_operations_as_csv_and_rejects_invalid_amount():
    # проверяет общий разбор суммы даты и контрагента
    forms = ManualTransactionFormSet(manual_data(), prefix="rows")
    assert forms.is_valid(), forms.errors
    rows, _ = read_transactions_csv(ContentFile((HEADER + ROW).encode()))
    assert forms[0].cleaned_data["operation"] == rows[0]
    assert rows[0]["occurred_at"] == datetime(2026, 10, 20, 11, 30, tzinfo=timezone.utc)
    for amount in ("0", "1.001", "NaN"):
        forms = ManualTransactionFormSet(manual_data(**{"rows-0-amount": amount}), prefix="rows")
        assert not forms.is_valid()
        assert "amount" in forms[0].errors


@pytest.mark.django_db
def test_manual_import_replaces_only_own_history_and_ignores_deleted_row(client):
    # проверяет замену своей истории без удаленной строки
    user = User.objects.create_user(username="anna")
    other = User.objects.create_user(username="ivan")
    original = import_transactions(user, ContentFile((HEADER + ROW).encode()))
    other_history = import_transactions(other, ContentFile((HEADER + ROW).encode()))
    client.force_login(user)
    data = manual_data(**{"rows-TOTAL_FORMS": "2", "rows-0-amount": "0.01",
                          "rows-0-recipient": "new_shop", "rows-1-DELETE": "on",
                          "rows-1-amount": "wrong", "user_id": other.pk})
    response = client.post(reverse("transactions:history"), data)
    assert response.status_code == 302
    assert response.url.endswith("?imported=1")
    assert not TransactionImport.objects.filter(pk=original.pk).exists()
    assert TransactionImport.objects.filter(pk=other_history.pk).exists()
    rows = get_user_transactions(user)
    assert len(rows) == 1 and rows[0]["amount_kopecks"] == 1
    assert rows[0]["recipient"] == "new_shop"
    assert TransactionImport.objects.get(user=user).file_sha256 == ""


@pytest.mark.django_db
def test_manual_error_and_add_row_preserve_history_and_entered_data(client):
    # проверяет ошибки и добавление строки без сохранения истории
    user = User.objects.create_user(username="anna")
    original = import_transactions(user, ContentFile((HEADER + ROW).encode()))
    client.force_login(user)
    url = reverse("transactions:history")
    response = client.post(url, manual_data(**{"rows-0-amount": "1.001"}))
    assert response.status_code == 200
    assert "amount" in response.context["manual_forms"][0].errors
    assert response.context["manual_forms"][0]["amount"].value() == "1.001"
    response = client.post(url, manual_data(action="add_row"))
    assert response.status_code == 200
    assert len(response.context["manual_forms"]) == 2
    assert response.context["manual_forms"][0]["recipient"].value() == "shop_1"
    forms = ManualTransactionFormSet(response.context["manual_forms"].data, prefix="rows")
    assert forms.is_valid(), forms.errors
    assert TransactionImport.objects.get(user=user).pk == original.pk
    assert len(get_user_transactions(user)) == 1


@pytest.mark.django_db
def test_manual_empty_tampered_and_oversized_formsets_do_not_replace_history(client):
    # проверяет границы формы и сохранность старых операций
    user = User.objects.create_user(username="anna")
    original = import_transactions(user, ContentFile((HEADER + ROW).encode()))
    client.force_login(user)
    for data in ({"action": "manual"}, manual_data(**{"rows-0-DELETE": "on"}),
                 manual_data(**{"rows-TOTAL_FORMS": str(MAX_MANUAL_ROWS + 1)})):
        response = client.post(reverse("transactions:history"), data)
        assert response.status_code == 200
        assert response.context["manual_forms"].non_form_errors()
        assert TransactionImport.objects.get(user=user).pk == original.pk


@pytest.mark.django_db
def test_manual_import_requires_login_and_csrf():
    # проверяет защиту ручной загрузки от чужого запроса
    url = reverse("transactions:history")
    assert Client().post(url, manual_data()).status_code == 302
    client = Client(enforce_csrf_checks=True)
    user = User.objects.create_user(username="anna")
    client.force_login(user)
    assert client.post(url, manual_data()).status_code == 403
    assert not TransactionImport.objects.exists()
