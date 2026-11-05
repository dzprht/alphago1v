from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest
from django.contrib.sessions.models import Session
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from accounts.models import User
from analysis.features import Turn
from analysis.pipeline import Analyzer
from analysis.text import TEXT_LABELS
from checks.models import Check


pytestmark = pytest.mark.django_db
TEXT = "Назовите код из сообщения"


def media_upload(kind):
    # готовит небольшой файл в разрешенном формате
    stream = BytesIO()
    Image.new("RGB", (640, 300), "white").save(stream, format="PNG")
    return SimpleUploadedFile("chat.png", stream.getvalue(), content_type="image/png")


class TextStub:
    def predict(self, turns, background="", order_known=True):
        # возвращает найденную просьбу с исходной цитатой
        return {"analyzer_version": "text-test", "turns": [
            {"turn_id": turn.turn_id, "speaker": turn.speaker, "owner": turn.owner,
             "values": {"text_" + label: 0.95 if label == "credentials_request" else 0.1
                        for label in TEXT_LABELS},
             "source_refs": {"text_credentials_request": {
                 "turn_id": turn.turn_id, "text": turn.text, "source_span": turn.source_span,
             }}} for turn in turns
        ]}


class MediaStub:
    def __init__(self):
        # запоминает временные файлы и выбранный исход
        self.paths = []
        self.status = "ok"
        self.failure = False

    def extract(self, path):
        # читает временный файл и возвращает распознанную реплику
        path = Path(path)
        self.paths.append(path)
        assert path.read_bytes()
        if self.failure:
            raise RuntimeError("private model details")
        return {"status": self.status, "analyzer_version": "media-test", "order_known": True,
                "turns": [] if self.status == "error" else [Turn("one", TEXT, "A")],
                "extracted_text": "" if self.status == "error" else TEXT,
                "limitations": ["Часть записи не распознана"] if self.status == "partial" else []}


@pytest.fixture
def configured_media(monkeypatch, settings):
    # подключает общий анализатор с подставным распознаванием
    settings.ANALYZER_DIR = "/test/models"
    analyzer = Analyzer(TextStub(), Mock(), {"text_" + label: 0.8 for label in TEXT_LABELS} | {"url_score": 0.8})
    media = MediaStub()
    loader = Mock(return_value=analyzer)
    media_loader = Mock(return_value=media)
    monkeypatch.setattr("checks.views.load_analyzer", loader)
    monkeypatch.setattr("checks.views.load_media_model", media_loader)
    return analyzer, media, loader, media_loader


@pytest.mark.parametrize("kind,field", [("screenshot", "image")])
@pytest.mark.parametrize("authenticated", [False, True])
def test_media_reaches_rules_and_temporary_file_is_removed(client, configured_media, kind, field, authenticated):
    # проверяет загрузку результат и удаление файла у гостя и аккаунта
    analyzer, media, loader, media_loader = configured_media
    if authenticated:
        client.force_login(User.objects.create_user(username="owner"))
    upload = media_upload(kind)
    response = client.post(reverse("checks:new", args=[kind]), {field: upload})
    assert response.status_code == (302 if authenticated else 200)
    check = Check.objects.get() if authenticated else response.context["check"]
    assert check.status == Check.Status.COMPLETED and check.extracted_text == TEXT
    assert check.result["warning"] and check.result["warning_reasons"]
    assert check.input_data[field] == {"name": upload.name, "size": upload.size}
    assert len(media.paths) == 1 and not media.paths[0].exists()
    assert not media.paths[0].parent.exists()
    assert loader.call_count == media_loader.call_count == 1
    assert Check.objects.count() == Session.objects.count() == int(authenticated)
    if authenticated:
        assert client.get(response.url).status_code == 200
        assert loader.call_count == media_loader.call_count == 1
    else:
        assert check.pk is None and check.context_snapshot == {}
        assert "Гостевой результат не сохранен" in response.content.decode()


def test_invalid_media_and_missing_weights_do_not_create_checks(client, configured_media):
    # отклоняет плохой файл до моделей и отсутствие весов до сохранения
    analyzer, media, loader, media_loader = configured_media
    url = reverse("checks:new", args=["screenshot"])
    assert client.post(url, {"image": SimpleUploadedFile("bad.png", b"not png")}).status_code == 400
    loader.assert_not_called()
    media_loader.assert_not_called()
    media_loader.side_effect = FileNotFoundError("private model path")
    response = client.post(url, {"image": media_upload("screenshot")})
    assert response.status_code == 503 and Check.objects.count() == Session.objects.count() == 0
    assert "private model path" not in response.content.decode() and media.paths == []


@pytest.mark.parametrize("failure", ["recognizer_error", "recognizer_exception", "analyzer_exception"])
def test_media_failures_remove_temporary_files(client, configured_media, failure):
    # удаляет файл при ошибке распознавания или последующего анализа
    analyzer, media, loader, media_loader = configured_media
    if failure == "recognizer_error":
        media.status = "error"
    elif failure == "recognizer_exception":
        media.failure = True
    else:
        analyzer.analyze = Mock(side_effect=RuntimeError("private analyzer details"))
    response = client.post(reverse("checks:new", args=["screenshot"]), {"image": media_upload("screenshot")})
    assert response.status_code == 503 and response.context["check"].status == Check.Status.FAILED
    assert len(media.paths) == 1 and not media.paths[0].exists() and not media.paths[0].parent.exists()
    assert "private" not in response.content.decode()
    assert Check.objects.count() == Session.objects.count() == 0


def test_partial_media_keeps_warning_and_csrf_prevents_processing(client, configured_media):
    # сохраняет найденное при неполной записи и проверяет токен загрузки
    analyzer, media, loader, media_loader = configured_media
    media.status = "partial"
    url = reverse("checks:new", args=["screenshot"])
    response = client.post(url, {"image": media_upload("screenshot")})
    result = response.context["check"].result
    assert response.status_code == 200 and result["status"] == "partial"
    assert result["warning"] and result["sufficiency"] == "limited"
    assert "Часть записи не распознана" in result["limitations"]
    protected = Client(enforce_csrf_checks=True)
    assert protected.post(url, {"image": media_upload("screenshot")}).status_code == 403
    assert loader.call_count == media_loader.call_count == 1
    assert Check.objects.count() == Session.objects.count() == 0
