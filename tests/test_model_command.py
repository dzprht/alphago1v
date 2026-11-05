from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from checks.management.commands import check_models


@pytest.mark.parametrize("mode", ["ready", "missing", "partial"])
def test_full_model_check_requires_all_models_and_diarization(monkeypatch, settings, mode):
    # проверяет готовый комплект и отказ без распознавания
    settings.ANALYZER_DIR = "/test/models"
    monkeypatch.setattr(check_models, "load_analyzer", Mock(return_value=object()))
    audio = None if mode == "missing" else SimpleNamespace(diarizer=object() if mode == "ready" else None)
    media = Mock(side_effect=[audio, object()])
    monkeypatch.setattr(check_models, "load_media_model", media)
    output = StringIO()
    if mode == "ready":
        call_command("check_models", stdout=output)
        assert "готовы к запуску" in output.getvalue() and media.call_count == 2
    else:
        with pytest.raises(CommandError, match="не готов"):
            call_command("check_models", stdout=output)
