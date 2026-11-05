import json
from io import BytesIO
import wave

import pytest
from PIL import Image
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import User
from checks.forms import (
    AudioCheckForm,
    ScreenshotCheckForm,
    TextCheckForm,
    TRANSFER_QUESTIONS,
    TransferCheckForm,
    URLCheckForm,
)
from checks.models import Check
from checks.services import create_check


@pytest.mark.parametrize(
    ("form_class", "kind", "data"),
    [
        (TextCheckForm, Check.Kind.TEXT, {"text": "Сообщение"}),
        (URLCheckForm, Check.Kind.URL, {"url": "https://example.com/"}),
        (AudioCheckForm, Check.Kind.AUDIO, {}),
        (ScreenshotCheckForm, Check.Kind.SCREENSHOT, {}),
        (TransferCheckForm, Check.Kind.TRANSFER, {"amount": "123.45", "purpose": "unknown"}),
    ],
)
def test_check_forms_prepare_valid_data(form_class, kind, data):
    # проверяет обязательные поля и подготовку данных
    files = {}
    if kind == Check.Kind.AUDIO:
        buffer = BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0" * 3200)
        content = buffer.getvalue()
        files["audio"] = SimpleUploadedFile("call.wav", content)
    elif kind == Check.Kind.SCREENSHOT:
        buffer = BytesIO()
        Image.new("RGB", (1, 1), "white").save(buffer, format="PNG")
        content = buffer.getvalue()
        files["image"] = SimpleUploadedFile("chat.png", content, content_type="image/png")

    form = form_class(data={**data, "user_id": 999, "status": "completed"}, files=files)
    assert form.is_valid(), form.errors
    payload = form.get_input_data()

    assert form.kind == kind
    if kind == Check.Kind.TRANSFER:
        assert "context" not in payload
        assert all(payload[name] == "unknown" for name in TRANSFER_QUESTIONS)
    else:
        assert payload["context"] == ""
    assert "user_id" not in payload
    assert "status" not in payload
    json.dumps(payload)
    for name, upload in files.items():
        assert payload[name] == {"name": upload.name, "size": upload.size}
        assert form.cleaned_data[name] is upload
        assert upload.read() == content
    if kind == Check.Kind.TRANSFER:
        assert payload["amount_kopecks"] == 12345
        assert "amount" not in payload
        assert payload["purpose"] == "unknown"


@pytest.mark.parametrize(
    ("form_class", "data", "error_field"),
    [
        (TextCheckForm, {"text": "   "}, "text"),
        (URLCheckForm, {"url": "ftp://example.com/"}, "url"),
        (AudioCheckForm, {"audio": "call.wav"}, "audio"),
        (ScreenshotCheckForm, {}, "image"),
        (TransferCheckForm, {"amount": "0", "purpose": "unknown"}, "amount"),
        (TransferCheckForm, {"amount": "1.001", "purpose": "unknown"}, "amount"),
        (TransferCheckForm, {"amount": "10000000000000000", "purpose": "unknown"}, "amount"),
        (TransferCheckForm, {"amount": "1", "purpose": "made_up"}, "purpose"),
        (TransferCheckForm, {"amount": "1", "purpose": ""}, "purpose"),
    ],
)
def test_check_forms_reject_invalid_data(form_class, data, error_field):
    # проверяет ошибки во входных данных
    files = {}
    if form_class is ScreenshotCheckForm:
        files["image"] = SimpleUploadedFile("chat.png", b"not an image", content_type="image/png")

    form = form_class(data=data, files=files)

    assert not form.is_valid()
    assert error_field in form.errors


def test_invalid_form_cannot_prepare_data():
    # проверяет запрет на использование данных с ошибками
    form = TextCheckForm(data={"text": ""})

    with pytest.raises(ValueError):
        form.get_input_data()


@pytest.mark.django_db
@pytest.mark.parametrize("signed_in", [False, True])
def test_form_data_can_create_check(signed_in):
    # проверяет сохранение формы для гостя и аккаунта
    user = User.objects.create_user(username="anna") if signed_in else AnonymousUser()
    form = TransferCheckForm(data={"amount": "9999999999999999.99", "purpose": "unknown"})

    check = create_check(user, form.kind, form.get_input_data())

    assert check.input_data["amount_kopecks"] == 999999999999999999
    if signed_in:
        check.refresh_from_db()
        assert check.user == user
        assert check.input_data["amount_kopecks"] == 999999999999999999
    else:
        assert check.pk is None
        assert Check.objects.count() == 0


def test_transfer_questionnaire_keeps_answers_separate_from_message():
    # проверяет ответы анкеты и игнорирование свободного пересказа
    answers = {
        "requester": "claimed_organization", "channel": "call", "urgency": "yes",
        "secrecy": "no", "changed_details": "unknown",
    }
    form = TransferCheckForm(data={
        "amount": "12.34", "purpose": "purchase", "recipient": " shop ",
        "message": "Переведите деньги", "context": "Не должно попасть во вход", **answers,
    })
    data = form.get_input_data()
    assert data == {"amount_kopecks": 1234, "purpose": "purchase", "recipient": "shop",
                    "message": "Переведите деньги", **answers}
    for name in TRANSFER_QUESTIONS:
        invalid = TransferCheckForm(data={"amount": "1", "purpose": "unknown", name: "made_up"})
        assert not invalid.is_valid()
        assert name in invalid.errors
    too_long = TransferCheckForm(data={"amount": "1", "purpose": "unknown", "recipient": "x" * 101})
    assert not too_long.is_valid()
    assert "recipient" in too_long.errors
