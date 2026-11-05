from io import BytesIO
from pathlib import Path
import wave

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from checks.forms import AudioCheckForm, ScreenshotCheckForm
from checks.media_limits import MAX_AUDIO_BYTES, MAX_IMAGE_BYTES


def wav_bytes(seconds=1, channels=1, rate=16000, width=2):
    # создает короткую запись для проверки формы
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0" * (seconds * channels * rate * width))
    return output.getvalue()


def image_bytes(format="PNG", size=(720, 800), animated=False):
    # создает простое изображение для проверки загрузки
    output = BytesIO()
    image = Image.new("RGB", size, "white")
    if animated:
        image.save(output, format=format, save_all=True, append_images=[Image.new("RGB", size, "black")])
    else:
        image.save(output, format=format)
    return output.getvalue()


@pytest.mark.parametrize("channels", [1, 2])
def test_audio_accepts_sixty_seconds_and_keeps_upload_readable(channels):
    # принимает запись на границе длительности и возвращает ее к началу
    content = wav_bytes(seconds=60, channels=channels)
    upload = SimpleUploadedFile("call.wav", content, content_type="application/octet-stream")
    form = AudioCheckForm(data={"participants": "Не используется"}, files={"audio": upload})
    assert form.is_valid(), form.errors
    assert upload.tell() == 0 and upload.read() == content
    assert "participants" not in form.get_input_data()


@pytest.mark.parametrize("content", [
    b"not a wave", wav_bytes()[:-10], wav_bytes(seconds=61),
    wav_bytes(rate=44100), wav_bytes(width=1), b"x" * (MAX_AUDIO_BYTES + 1),
])
def test_audio_rejects_invalid_content_and_limits(content):
    # отклоняет поддельную обрезанную и неподходящую запись
    upload = SimpleUploadedFile("call.wav", content, content_type="audio/wav")
    form = AudioCheckForm(data={}, files={"audio": upload})
    assert not form.is_valid() and "audio" in form.errors
    assert upload.tell() == 0


@pytest.mark.parametrize("format", ["PNG", "JPEG"])
def test_screenshot_accepts_supported_content_and_preserves_metadata(format):
    # принимает изображение без ручного указания сторон переписки
    content = image_bytes(format=format, size=(2000, 2000))
    upload = SimpleUploadedFile("chat.bin", content, content_type="application/octet-stream")
    form = ScreenshotCheckForm(data={"own_messages": "Не используется"}, files={"image": upload})
    assert form.is_valid(), form.errors
    assert upload.tell() == 0 and not upload.closed
    assert form.get_input_data() == {"context": "", "image": {"name": "chat.bin", "size": len(content)}}
    assert upload.read() == content


@pytest.mark.parametrize("content", [
    b"not an image", image_bytes(format="GIF"), image_bytes(animated=True),
    image_bytes(size=(2001, 2000)), b"x" * (MAX_IMAGE_BYTES + 1),
])
def test_screenshot_rejects_unsupported_or_excessive_content(content):
    # отклоняет неподходящий формат анимацию и превышение ограничений
    upload = SimpleUploadedFile("chat.png", content, content_type="image/png")
    form = ScreenshotCheckForm(data={}, files={"image": upload})
    assert not form.is_valid() and "image" in form.errors
    assert upload.tell() == 0


@pytest.mark.parametrize("name", ["warning.png", "ordinary.png", "warning.wav", "ordinary.wav"])
def test_demo_files_fit_the_actual_upload_forms(name):
    # проверяет примеры для защиты теми же формами что и сайт
    path = Path(__file__).resolve().parents[1] / "examples" / "demo" / name
    field, form_class = ("audio", AudioCheckForm) if path.suffix == ".wav" else ("image", ScreenshotCheckForm)
    form = form_class(data={}, files={field: SimpleUploadedFile(name, path.read_bytes())})
    assert form.is_valid(), form.errors
