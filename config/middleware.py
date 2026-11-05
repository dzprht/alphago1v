from ipaddress import ip_address

from django.conf import settings
from django.http import HttpResponseBadRequest


class TrustedProxyMiddleware:
    def __init__(self, get_response):
        # сохраняет следующий обработчик запроса
        self.get_response = get_response

    def __call__(self, request):
        # берет адрес посетителя только от доверенного прокси
        if request.META.get("REMOTE_ADDR") in settings.TRUSTED_PROXY_IPS:
            value = request.META.get("HTTP_X_REAL_IP", "").strip()
            try:
                address = ip_address(value)
                if "%" in value:
                    raise ValueError
            except ValueError:
                return HttpResponseBadRequest("Прокси не передал корректный IP посетителя")
            request.META["REMOTE_ADDR"] = str(address)
        return self.get_response(request)
