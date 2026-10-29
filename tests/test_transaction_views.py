import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from accounts.models import User
from transactions.csv_import import MAX_CSV_BYTES
from transactions.models import TransactionImport
from transactions.services import get_user_transactions, import_transactions


HEADER = "occurred_at;amount;direction;recipient\n"
ROW = "2026-10-20T14:30:00+03:00;1500.50;outgoing;shop_1\n"
pytestmark = pytest.mark.django_db


def test_transactions_require_login(client):
    # проверяет закрытый доступ к истории загрузке и удалению
    login_url = reverse("accounts:login")
    for name in ["history", "template"]:
        response = client.get(reverse(f"transactions:{name}"))
        assert response.status_code == 302
        assert response.url.startswith(login_url)
    response = client.post(reverse("transactions:history"), {})
    assert response.status_code == 302
    response = client.post(reverse("transactions:delete"), {})
    assert response.status_code == 302
    assert TransactionImport.objects.count() == 0


def test_owner_can_download_csv_template(client):
    # проверяет скачивание готового файла после входа
    user = User.objects.create_user(username="anna")
    client.force_login(user)

    response = client.get(reverse("transactions:template"))
    content = b"".join(response.streaming_content)
    response.close()

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert response["Content-Disposition"] == 'attachment; filename="transactions.csv"'
    assert "no-store" in response["Cache-Control"]
    assert content.startswith(HEADER.encode())
    assert b"shop_1" in content


def test_upload_and_history_use_only_logged_in_owner(client):
    # проверяет загрузку и просмотр только своих операций
    user = User.objects.create_user(username="anna")
    other_user = User.objects.create_user(username="ivan")
    other_history = import_transactions(
        other_user, ContentFile((HEADER + ROW.replace("shop_1", "other_shop")).encode())
    )
    client.force_login(user)
    history_url = reverse("transactions:history")

    response = client.post(
        history_url,
        {
            "file": SimpleUploadedFile("history.csv", (HEADER + ROW + ROW).encode()),
            "user_id": other_user.pk,
        },
    )

    assert response.status_code == 302
    assert response.url == history_url + "?imported=1"
    response = client.get(response.url)
    assert response.status_code == 200
    assert response.context["imported"] is True
    assert len(response.context["transactions"]) == 2
    assert all(row["recipient"] == "shop_1" for row in response.context["transactions"])
    assert "other_shop" not in response.content.decode()
    assert "no-store" in response["Cache-Control"]
    assert TransactionImport.objects.get(user=other_user).pk == other_history.pk
    assert len(get_user_transactions(other_user)) == 1


def test_invalid_upload_preserves_old_history(client):
    # проверяет ошибку поздней строки без потери старой истории
    user = User.objects.create_user(username="anna")
    old_history = import_transactions(user, ContentFile((HEADER + ROW).encode()))
    old_rows = get_user_transactions(user)
    client.force_login(user)
    content = HEADER + ROW + ROW.replace("1500.50", "-10")

    response = client.post(
        reverse("transactions:history"),
        {"file": SimpleUploadedFile("history.csv", content.encode())},
    )

    assert response.status_code == 200
    assert "Строка 3 поле amount" in str(response.context["form"].errors["file"])
    assert len(response.context["transactions"]) == 1
    assert TransactionImport.objects.get(user=user).pk == old_history.pk
    assert get_user_transactions(user) == old_rows


def test_upload_requires_file_and_checks_size(client):
    # проверяет отсутствие файла и превышение размера
    user = User.objects.create_user(username="anna")
    client.force_login(user)
    history_url = reverse("transactions:history")

    response = client.post(history_url, {})
    assert response.status_code == 200
    assert "file" in response.context["form"].errors
    response = client.post(
        history_url,
        {"file": SimpleUploadedFile("history.csv", b"x" * (MAX_CSV_BYTES + 1))},
    )
    assert response.status_code == 200
    assert "2 МиБ" in str(response.context["form"].errors["file"])
    assert TransactionImport.objects.count() == 0


def test_delete_requires_post_and_preserves_other_history(client):
    # проверяет удаление своей истории только отправкой формы
    user = User.objects.create_user(username="anna")
    other_user = User.objects.create_user(username="ivan")
    history = import_transactions(user, ContentFile((HEADER + ROW).encode()))
    other_history = import_transactions(other_user, ContentFile((HEADER + ROW).encode()))
    client.force_login(user)
    delete_url = reverse("transactions:delete")

    response = client.get(delete_url)
    assert response.status_code == 405
    assert TransactionImport.objects.filter(pk=history.pk).exists()
    response = client.post(delete_url, {"user_id": other_user.pk})
    assert response.status_code == 302
    assert response.url == reverse("transactions:history") + "?deleted=1"
    assert get_user_transactions(user) == []
    assert TransactionImport.objects.filter(pk=other_history.pk).exists()
    response = client.get(response.url)
    assert response.context["deleted"] is True
    assert response.context["transactions"] == []


def test_transaction_posts_require_csrf():
    # проверяет защиту токеном при загрузке и удалении истории
    user = User.objects.create_user(username="anna")
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    history_url = reverse("transactions:history")
    delete_url = reverse("transactions:delete")
    client.get(history_url)

    response = client.post(
        history_url, {"file": SimpleUploadedFile("history.csv", (HEADER + ROW).encode())}
    )
    assert response.status_code == 403
    assert TransactionImport.objects.count() == 0
    response = client.post(
        history_url,
        {
            "file": SimpleUploadedFile("history.csv", (HEADER + ROW).encode()),
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        },
    )
    assert response.status_code == 302
    history = TransactionImport.objects.get(user=user)
    response = client.post(delete_url, {})
    assert response.status_code == 403
    assert TransactionImport.objects.filter(pk=history.pk).exists()
    response = client.post(
        delete_url, {"csrfmiddlewaretoken": client.cookies["csrftoken"].value}
    )
    assert response.status_code == 302
    assert get_user_transactions(user) == []


def test_history_escapes_counterparty_id(client):
    # проверяет экранирование текста из файла на странице
    user = User.objects.create_user(username="anna")
    content = HEADER + ROW.replace("shop_1", "<script>alert(1)</script>")
    import_transactions(user, ContentFile(content.encode()))
    client.force_login(user)

    response = client.get(reverse("transactions:history"))

    assert response.status_code == 200
    html = response.content.decode()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
