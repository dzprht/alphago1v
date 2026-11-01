from runpy import run_path

import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import reverse


@pytest.mark.parametrize("enabled", [False, True])
def test_https_environment_controls_redirect_and_cookies(client, monkeypatch, enabled):
    # проверяет режим защищенного соединения из окружения
    monkeypatch.setenv("DJANGO_HTTPS", str(int(enabled)))
    config = run_path(str(settings.BASE_DIR / "config/settings.py"))
    flags = {name: config[name] for name in (
        "SECURE_SSL_REDIRECT", "SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE"
    )}
    assert all(value is enabled for value in flags.values())
    with override_settings(**flags):
        url = reverse("accounts:login")
        assert client.get(url).status_code == (301 if enabled else 200)
        response = client.get(url, secure=True)
        assert response.status_code == 200
        assert bool(response.cookies["csrftoken"]["secure"]) is enabled
        assert response["X-Frame-Options"] == "DENY"
