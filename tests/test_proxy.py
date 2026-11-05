from unittest.mock import Mock

import pytest
from django.http import HttpResponse

from config.middleware import TrustedProxyMiddleware


def test_trusted_proxy_passes_normalized_client_ip(rf, settings):
    # заменяет адрес прокси настоящим адресом посетителя
    settings.TRUSTED_PROXY_IPS = {"127.0.0.1"}
    request = rf.get("/", REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP="2001:0db8:0000:0000:0000:0000:0000:0007")
    handler = Mock(return_value=HttpResponse("ok"))
    assert TrustedProxyMiddleware(handler)(request).status_code == 200
    assert request.META["REMOTE_ADDR"] == "2001:db8::7"
    handler.assert_called_once_with(request)


@pytest.mark.parametrize("trusted", [set(), {"127.0.0.1"}])
def test_untrusted_client_cannot_replace_ip(rf, settings, trusted):
    # игнорирует поддельные заголовки от обычного посетителя
    settings.TRUSTED_PROXY_IPS = trusted
    request = rf.get("/", REMOTE_ADDR="203.0.113.7", HTTP_X_REAL_IP="198.51.100.9",
                     HTTP_X_FORWARDED_FOR="198.51.100.9")
    handler = Mock(return_value=HttpResponse("ok"))
    assert TrustedProxyMiddleware(handler)(request).status_code == 200
    assert request.META["REMOTE_ADDR"] == "203.0.113.7"


@pytest.mark.parametrize("value", ["", "not an ip", "192.0.2.1,192.0.2.2", "fe80::1%eth0"])
def test_trusted_proxy_must_supply_one_valid_ip(rf, settings, value):
    # отклоняет неверный адрес вместо общего лимита для всех
    settings.TRUSTED_PROXY_IPS = {"127.0.0.1"}
    request = rf.get("/", REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP=value)
    handler = Mock()
    assert TrustedProxyMiddleware(handler)(request).status_code == 400
    handler.assert_not_called()
