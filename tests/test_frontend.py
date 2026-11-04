import re

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse

from accounts.models import User
from checks.forms import TRAINING_CONSENT_VERSION
from checks.models import Check
from checks.services import create_check, finish_check
from transactions.services import import_transactions


pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("authenticated", [False, True])
def test_layout_supports_keyboard_navigation_and_account_links(client, authenticated):
    # проверяет общий каркас и ссылки для гостя и аккаунта
    if authenticated:
        client.force_login(User.objects.create_user(username="anna"))
    html = client.get(reverse("home")).content.decode()

    assert '<html lang="ru">' in html
    assert 'name="viewport"' in html
    assert 'href="/static/css/site.css"' in html
    assert 'href="#content"' in html
    assert re.search(r'<main\b[^>]*\bid="content"', html)
    assert len(re.findall(r'<h1\b', html)) == 1
    navigation = re.search(r'<nav\b[^>]*>(.*?)</nav>', html, re.DOTALL).group(1)
    for name in ["accounts:profile", "checks:history", "transactions:history"]:
        assert (f'href="{reverse(name)}"' in navigation) is authenticated
    for name in ["accounts:login", "accounts:register"]:
        assert (f'href="{reverse(name)}"' in navigation) is not authenticated


def test_invalid_url_keeps_accessible_help_errors_and_escaped_input(client):
    # проверяет связь поля с подсказкой и ошибкой без потери ввода
    response = client.post(reverse("checks:new", args=["url"]), {"url": "<script>alert(1)</script>"})
    html = response.content.decode()

    assert response.status_code == 400
    assert 'for="id_url"' in html
    assert 'id="id_url_helptext"' in html
    assert 'id="id_url_error"' in html
    assert 'aria-describedby="id_url_helptext id_url_error"' in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
    assert '<script>' not in html


def test_consent_form_keeps_version_optional_checkbox_and_visible_errors(client):
    # проверяет скрытую версию условий и видимую ошибку согласия
    user = User.objects.create_user(username="anna")
    check = create_check(user, Check.Kind.TEXT, {"text": "Пример"})
    finish_check(check, result={"explanations": ["Пример"]}, analyzer_version="test-v1")
    client.force_login(user)
    url = reverse("checks:detail", args=[check.pk])
    html = client.get(url).content.decode()
    forms = re.findall(r'<form\b[^>]*>(.*?)</form>', html, re.DOTALL)
    consent = next(form for form in forms if 'value="consent"' in form)
    checkbox = re.search(r'<input\b[^>]*\bname="granted"[^>]*>', consent).group()
    version = re.search(r'<input\b[^>]*\bname="terms_version"[^>]*>', consent).group()

    assert 'type="checkbox"' in checkbox and 'checked' not in checkbox
    assert 'type="hidden"' in version and f'value="{TRAINING_CONSENT_VERSION}"' in version
    assert 'name="outcome"' not in consent
    response = client.post(url, {"action": "consent", "terms_version": "old"})
    error = response.context["consent_form"].errors["terms_version"][0]
    assert error in response.content.decode()


def test_csv_table_has_caption_column_headers_and_scroll_container(client):
    # проверяет понятную таблицу операций для узкого экрана
    user = User.objects.create_user(username="anna")
    import_transactions(user, ContentFile(
        b"occurred_at;amount;direction;recipient\n2026-10-20T14:30:00+03:00;1500.50;outgoing;shop_1\n"
    ))
    client.force_login(user)
    html = client.get(reverse("transactions:history")).content.decode()

    assert re.search(r'class="[^"]*\btable-scroll\b[^"]*"[^>]*>\s*<table\b', html)
    assert re.search(r'<caption\b[^>]*>\s*Загруженные операции\s*</caption>', html)
    assert len(re.findall(r'<th\b[^>]*\bscope="col"', html)) == 4
    assert 'shop_1' in html
