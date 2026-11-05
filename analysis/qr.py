import cv2
import numpy as np
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator


def decode_qr(content):
    # достает одну ссылку из изображения не открывая сайт
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Не удалось прочитать изображение QR-кода")
    detector = cv2.QRCodeDetector()
    found, points = detector.detectMulti(image)
    if not found:
        raise ValueError("QR-код не найден. Загрузите четкое изображение целого кода.")
    if len(points) != 1:
        raise ValueError("На изображении должен быть только один QR-код")
    try:
        value, _ = detector.decode(image, points)
    except (UnicodeDecodeError, cv2.error):
        raise ValueError("Не удалось прочитать содержимое QR-кода") from None
    value = value.strip()
    if not value:
        raise ValueError("Не удалось прочитать QR-код. Попробуйте более четкое изображение.")
    try:
        URLValidator(schemes=["http", "https"])(value)
    except ValidationError:
        raise ValueError("QR-код должен содержать полную ссылку с http:// или https:// до 2048 символов") from None
    return value
