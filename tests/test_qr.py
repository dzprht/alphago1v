from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from accounts.models import User
from analysis.qr import decode_qr
from checks.forms import QRCheckForm
from checks.models import Check


URL = "https://www.alfabank.ru"


def qr_pixels(value):
    # создает четкий код для проверки настоящего декодера
    pixels = np.pad(cv2.QRCodeEncoder_create().encode(value), 4, constant_values=255)
    return cv2.resize(pixels, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)


@pytest.mark.parametrize("value", [URL, "tel:900", "Обычный текст", "https://example.com/" + "x" * 2048])
def test_qr_accepts_only_a_complete_web_url(value):
    # проверяет ссылку и отказ от других типов содержимого
    content = cv2.imencode(".png", qr_pixels(value))[1].tobytes()
    if value == URL:
        assert decode_qr(content) == URL
    else:
        with pytest.raises(ValueError):
            decode_qr(content)


def test_qr_rejects_blank_image_and_multiple_codes():
    # не выбирает случайную ссылку из нескольких кодов
    for pixels in (np.full((400, 400), 255, dtype=np.uint8),
                   np.concatenate([qr_pixels(URL), qr_pixels("https://www.example.com")], axis=1)):
        content = cv2.imencode(".png", pixels)[1].tobytes()
        with pytest.raises(ValueError):
            decode_qr(content)


@pytest.mark.django_db
@pytest.mark.parametrize("authenticated", [False, True])
def test_qr_passes_decoded_url_to_existing_analyzer(client, monkeypatch, settings, authenticated):
    # проверяет общий анализ ссылки и сохранение только после входа
    settings.ANALYZER_DIR = "/test/models"
    if authenticated:
        client.force_login(User.objects.create_user(username="qr_owner"))
    analyzer = Mock()
    analyzer.analyze.return_value = ({"status": "ok", "warning": False, "policy": "warning-rules-v1",
                                      "sufficiency": "sufficient", "analyzer_version": "url-test",
                                      "findings": []}, {})
    monkeypatch.setattr("checks.views.load_analyzer", Mock(return_value=analyzer))
    media = Mock()
    monkeypatch.setattr("checks.views.load_media_model", media)
    path = Path(__file__).resolve().parents[1] / "examples/demo/qr-ordinary.png"
    upload = SimpleUploadedFile(path.name, path.read_bytes(), content_type="image/png")
    response = client.post(reverse("checks:new", args=["qr"]), {
        "image": upload, "expected_site": "Альфа-Банк", "message": "Просьба проверить ссылку",
    })
    assert response.status_code == (302 if authenticated else 200)
    check = Check.objects.get() if authenticated else response.context["check"]
    assert check.kind == "qr" and check.status == "completed"
    assert check.extracted_text == check.input_data["url"] == URL
    assert check.input_data["expected_site"] == "Альфа-Банк"
    assert check.input_data["image"] == {"name": path.name, "size": upload.size}
    assert analyzer.analyze.call_args.args[0] == Check.Kind.URL
    assert analyzer.analyze.call_args.args[1] == check.input_data
    assert "extraction" not in analyzer.analyze.call_args.kwargs
    media.assert_not_called()
    assert Check.objects.count() == int(authenticated)


def test_invalid_qr_is_an_error_of_the_form():
    # не выдает отсутствие ссылки за успешную проверку
    stream = BytesIO()
    Image.new("RGB", (300, 300), "white").save(stream, "PNG")
    form = QRCheckForm(data={}, files={"image": SimpleUploadedFile("blank.png", stream.getvalue())})
    assert not form.is_valid() and "QR-код не найден" in form.errors["image"][0]
