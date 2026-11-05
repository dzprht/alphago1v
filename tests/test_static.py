from django.conf import settings
from django.core.management import call_command
from django.test import Client, override_settings


def test_collected_css_is_served_without_debug(tmp_path):
    # проверяет выдачу стилей и скрипта ручного ввода
    with override_settings(DEBUG=False, STATIC_ROOT=tmp_path, SECURE_SSL_REDIRECT=False):
        call_command("collectstatic", interactive=False, verbosity=0)
        for name, content_type in (("css/site.css", "text/css"), ("js/transactions.js", "text/javascript")):
            source = settings.BASE_DIR / "static" / name
            assert (tmp_path / name).read_bytes() == source.read_bytes()
            response = Client().get("/static/" + name)
            assert response.status_code == 200
            assert response["Content-Type"].startswith(content_type)
            assert b"".join(response.streaming_content) == source.read_bytes()
