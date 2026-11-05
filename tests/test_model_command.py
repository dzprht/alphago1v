from io import StringIO
from unittest.mock import Mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from checks.management.commands import check_models


@pytest.mark.parametrize("ready", [False, True])
def test_full_model_check_requires_ocr_but_not_audio(monkeypatch, settings, ready):
    # проверяет готовность моделей без загрузки распознавания речи
    settings.ANALYZER_DIR = "/test/models"
    monkeypatch.setattr(check_models, "load_analyzer", Mock(return_value=object()))
    media = Mock(return_value=object() if ready else None)
    monkeypatch.setattr(check_models, "load_media_model", media)
    output = StringIO()
    if ready:
        call_command("check_models", stdout=output)
        assert "QR" in output.getvalue() and "готовы к запуску" in output.getvalue()
    else:
        with pytest.raises(CommandError, match="не готов"):
            call_command("check_models", stdout=output)
    media.assert_called_once_with("screenshot", settings.ANALYZER_DIR)
