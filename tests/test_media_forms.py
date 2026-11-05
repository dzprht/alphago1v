from io import BytesIO
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from checks.forms import QRCheckForm, ScreenshotCheckForm
from checks.media_limits import MAX_IMAGE_BYTES


def image_bytes(format="PNG", size=(720, 800), animated=False):
    # создает простое изображение для проверки загрузки
    output = BytesIO()
    image = Image.new("RGB", size, "white")
    if animated:
        image.save(output, format=format, save_all=True, append_images=[Image.new("RGB", size, "black")])
    else:
        image.save(output, format=format)
    return output.getvalue()


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


@pytest.mark.parametrize("name", ["warning.png", "ordinary.png", "qr-warning.png", "qr-ordinary.png"])
def test_demo_files_fit_the_actual_upload_forms(name):
    # проверяет примеры для защиты теми же формами что и сайт
    path = Path(__file__).resolve().parents[1] / "examples" / "demo" / name
    field, form_class = "image", QRCheckForm if name.startswith("qr-") else ScreenshotCheckForm
    form = form_class(data={}, files={field: SimpleUploadedFile(name, path.read_bytes())})
    assert form.is_valid(), form.errors
