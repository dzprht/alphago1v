from django.conf import settings
from django.core.management import call_command
from django.test import Client, override_settings


def test_collected_css_is_served_without_debug(tmp_path):
    # проверяет сборку и выдачу стилей в рабочем режиме
    source = settings.BASE_DIR / "static/css/site.css"
    with override_settings(DEBUG=False, STATIC_ROOT=tmp_path, SECURE_SSL_REDIRECT=False):
        call_command("collectstatic", interactive=False, verbosity=0)
        assert (tmp_path / "css/site.css").read_bytes() == source.read_bytes()
        response = Client().get("/static/css/site.css")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/css")
        assert b"".join(response.streaming_content) == source.read_bytes()
