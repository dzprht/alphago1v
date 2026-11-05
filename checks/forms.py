from decimal import Decimal
from io import BytesIO

from django import forms
from django.core.files import File
from django.core.validators import URLValidator
from PIL import Image, UnidentifiedImageError

from checks.media_limits import (
    MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS,
)
from checks.models import Check, CheckFeedback
from analysis.qr import decode_qr


TRAINING_CONSENT_VERSION = "2026-10-28"
TRANSFER_QUESTIONS = ("requester", "channel", "urgency", "secrecy", "changed_details")
YES_NO_UNKNOWN = [("unknown", "Неизвестно"), ("yes", "Да"), ("no", "Нет")]


class CheckForm(forms.Form):
    context = forms.CharField(
        label="Дополнительные обстоятельства", required=False, max_length=2000, widget=forms.Textarea
    )

    def get_input_data(self):
        # готовит проверенные данные для обработки
        if not self.is_valid():
            raise ValueError("Нельзя использовать данные формы с ошибками")

        data = self.cleaned_data.copy()
        for name, value in data.items():
            if isinstance(value, File):
                data[name] = {"name": value.name, "size": value.size}
        return data


class TextCheckForm(CheckForm):
    kind = Check.Kind.TEXT
    text = forms.CharField(label="Текст сообщения или диалога", max_length=10000, widget=forms.Textarea)
    field_order = ["text", "context"]


class URLCheckForm(CheckForm):
    kind = Check.Kind.URL
    url = forms.CharField(
        label="Ссылка",
        max_length=2048,
        validators=[URLValidator(schemes=["http", "https"])],
        help_text="Полный адрес с http:// или https://",
    )
    message = forms.CharField(
        label="Сопроводительное сообщение", required=False, max_length=10000, widget=forms.Textarea
    )
    expected_site = forms.CharField(
        label="Ожидаемый сайт или организация", required=False, max_length=255,
        widget=forms.TextInput(attrs={"list": "expected-organizations"}),
        help_text="Выберите организацию или введите ее название или адрес сайта. "
                  "Совпадение домена не подтверждает личность отправителя.",
    )
    field_order = ["url", "message", "expected_site", "context"]


class ScreenshotCheckForm(CheckForm):
    kind = Check.Kind.SCREENSHOT
    image = forms.FileField(
        label="Скриншот переписки",
        widget=forms.ClearableFileInput(attrs={"accept": ".png,.jpg,.jpeg,image/png,image/jpeg"}),
        help_text="PNG или JPEG, одно изображение, до 5 МиБ и 4 миллионов пикселей. "
                  "Стороны переписки определяются автоматически.",
    )
    field_order = ["image", "context"]

    def clean_image(self):
        # проверяет формат размер и содержимое изображения
        upload = self.cleaned_data["image"]
        if upload.size > MAX_IMAGE_BYTES:
            raise forms.ValidationError("Изображение должно быть не больше 5 МиБ")
        try:
            with Image.open(BytesIO(upload.read())) as image:
                if image.format not in ("PNG", "JPEG") or getattr(image, "n_frames", 1) != 1:
                    raise forms.ValidationError("Нужно одно изображение PNG или JPEG без анимации")
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise forms.ValidationError("Изображение должно содержать не больше 4 миллионов пикселей")
                image.load()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            raise forms.ValidationError("Не удалось прочитать изображение PNG или JPEG") from None
        finally:
            upload.seek(0)
        return upload


class QRCheckForm(ScreenshotCheckForm):
    kind = Check.Kind.QR
    image = forms.FileField(
        label="Изображение QR-кода",
        widget=forms.ClearableFileInput(attrs={"accept": ".png,.jpg,.jpeg,image/png,image/jpeg"}),
        help_text="Один QR-код со ссылкой. PNG или JPEG, до 5 МиБ и 4 миллионов пикселей.",
    )
    message = forms.CharField(label="Сопроводительное сообщение", required=False,
                              max_length=10000, widget=forms.Textarea)
    expected_site = forms.CharField(label="Ожидаемый сайт или организация", required=False,
                                    max_length=255, widget=forms.TextInput(attrs={"list": "expected-organizations"}))
    field_order = ["image", "message", "expected_site", "context"]

    def clean_image(self):
        # проверяет изображение и достает ссылку до запуска модели
        upload = super().clean_image()
        try:
            self.decoded_url = decode_qr(upload.read())
        except ValueError as error:
            raise forms.ValidationError(str(error)) from None
        finally:
            upload.seek(0)
        return upload

    def get_input_data(self):
        # добавляет прочитанную ссылку к данным проверки
        data = super().get_input_data()
        data["url"] = self.decoded_url
        return data


class TransferCheckForm(CheckForm):
    kind = Check.Kind.TRANSFER
    context = None
    amount = forms.DecimalField(
        label="Сумма в рублях",
        min_value=Decimal("0.01"),
        max_digits=18,
        decimal_places=2,
    )
    purpose = forms.ChoiceField(
        label="Назначение перевода",
        choices=[
            ("", "Выберите назначение"),
            ("purchase", "Покупка"),
            ("debt", "Возврат долга"),
            ("gift", "Подарок или помощь"),
            ("own_accounts", "Между своими счетами"),
            ("other", "Другое"),
            ("unknown", "Неизвестно"),
        ],
    )
    recipient = forms.CharField(label="Обозначение получателя", max_length=100, required=False)
    message = forms.CharField(
        label="Текст просьбы о переводе", required=False, max_length=10000, widget=forms.Textarea
    )
    requester = forms.ChoiceField(
        label="Кто попросил перевод", required=False, initial="unknown",
        choices=[
            ("unknown", "Неизвестно"),
            ("self", "Мое решение"),
            ("acquaintance", "Знакомый человек"),
            ("seller", "Продавец"),
            ("claimed_organization", "Представился сотрудником организации"),
            ("other", "Другой человек"),
        ],
    )
    channel = forms.ChoiceField(
        label="Канал обращения", required=False, initial="unknown",
        choices=[
            ("unknown", "Неизвестно"),
            ("chat", "Чат"),
            ("call", "Звонок"),
            ("email", "Электронная почта"),
            ("website", "Сайт"),
            ("in_person", "Лично"),
            ("not_applicable", "Обращения не было"),
            ("other", "Другое"),
        ],
    )
    urgency = forms.ChoiceField(
        label="Вас торопят", choices=YES_NO_UNKNOWN, required=False, initial="unknown"
    )
    secrecy = forms.ChoiceField(
        label="Просят сохранить перевод в тайне", choices=YES_NO_UNKNOWN, required=False, initial="unknown"
    )
    changed_details = forms.ChoiceField(
        label="Реквизиты менялись", choices=YES_NO_UNKNOWN, required=False, initial="unknown"
    )
    field_order = ["amount", "purpose", "recipient", "message", *TRANSFER_QUESTIONS]

    def get_input_data(self):
        # готовит сумму и ответы анкеты для обработки
        data = super().get_input_data()
        data["amount_kopecks"] = int(data.pop("amount") * 100)
        for name in TRANSFER_QUESTIONS:
            data[name] = data[name] or "unknown"
        return data


class CheckFeedbackForm(forms.Form):
    outcome = forms.ChoiceField(
        label="Чем закончилась ситуация",
        choices=[("", "Выберите исход"), *CheckFeedback.Outcome.choices],
    )
    comment = forms.CharField(
        label="Комментарий", required=False, max_length=2000, widget=forms.Textarea
    )


class TrainingConsentForm(forms.Form):
    granted = forms.BooleanField(
        label="Разрешаю использовать данные этой проверки и мой отзыв "
        "для подготовки будущего обучения после ручной проверки данных",
        required=False,
        initial=False,
    )
    terms_version = forms.ChoiceField(
        choices=[(TRAINING_CONSENT_VERSION, TRAINING_CONSENT_VERSION)],
        initial=TRAINING_CONSENT_VERSION,
        widget=forms.HiddenInput,
    )
