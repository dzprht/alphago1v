from decimal import Decimal

from django import forms
from django.core.files import File
from django.core.validators import URLValidator

from checks.models import Check


class CheckForm(forms.Form):
    context = forms.CharField(
        label="Дополнительные обстоятельства", required=False, widget=forms.Textarea
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
    text = forms.CharField(label="Текст сообщения или диалога", widget=forms.Textarea)
    field_order = ["text", "context"]


class URLCheckForm(CheckForm):
    kind = Check.Kind.URL
    url = forms.CharField(
        label="Ссылка",
        validators=[URLValidator(schemes=["http", "https"])],
        help_text="Полный адрес с http:// или https://",
    )
    message = forms.CharField(
        label="Сопроводительное сообщение", required=False, widget=forms.Textarea
    )
    expected_site = forms.CharField(label="Ожидаемый сайт или организация", required=False)
    field_order = ["url", "message", "expected_site", "context"]


class AudioCheckForm(CheckForm):
    kind = Check.Kind.AUDIO
    audio = forms.FileField(label="Аудиозапись")
    participants = forms.CharField(
        label="Кто участвует в разговоре", required=False, widget=forms.Textarea
    )
    field_order = ["audio", "participants", "context"]


class ScreenshotCheckForm(CheckForm):
    kind = Check.Kind.SCREENSHOT
    image = forms.ImageField(label="Скриншот переписки")
    own_messages = forms.CharField(
        label="Какие реплики принадлежат вам", required=False, widget=forms.Textarea
    )
    field_order = ["image", "own_messages", "context"]


class TransferCheckForm(CheckForm):
    kind = Check.Kind.TRANSFER
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
    recipient = forms.CharField(label="Обозначение получателя", required=False)
    message = forms.CharField(
        label="Текст просьбы о переводе", required=False, widget=forms.Textarea
    )
    field_order = ["amount", "purpose", "recipient", "message", "context"]

    def get_input_data(self):
        # переводит сумму в целое число копеек
        data = super().get_input_data()
        data["amount_kopecks"] = int(data.pop("amount") * 100)
        return data
