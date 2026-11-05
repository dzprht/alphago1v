from django import forms

from transactions.csv_import import MAX_CSV_BYTES
from transactions.models import Transaction
from transactions.validation import validate_transaction


MAX_MANUAL_ROWS = 100


class ImportModeForm(forms.Form):
    replace = forms.BooleanField(label="Заменить всю историю", required=False)


class TransactionUploadForm(ImportModeForm):
    file = forms.FileField(label="История операций в CSV")

    def clean_file(self):
        # проверяет размер выбранного файла
        file = self.cleaned_data["file"]
        if file.size > MAX_CSV_BYTES:
            raise forms.ValidationError("Размер CSV не должен превышать 2 МиБ")
        return file


class ManualTransactionForm(forms.Form):
    occurred_at = forms.DateTimeField(
        label="Дата и время",
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
        help_text="Московское время",
    )
    amount = forms.CharField(label="Сумма в рублях", widget=forms.TextInput(attrs={"inputmode": "decimal"}))
    direction = forms.ChoiceField(label="Направление", choices=Transaction.Direction.choices,
                                  initial=Transaction.Direction.OUTGOING)
    recipient = forms.CharField(label="Обозначение контрагента", required=False, max_length=100)

    def clean(self):
        # проверяет строку теми же правилами что и csv
        data = super().clean()
        if not self.errors:
            try:
                data["operation"] = validate_transaction(
                    data["occurred_at"], data["amount"], data["direction"], data["recipient"]
                )
            except forms.ValidationError as error:
                for field, messages in error.message_dict.items():
                    self.add_error(field, messages)
        return data


ManualTransactionFormSet = forms.formset_factory(
    ManualTransactionForm, extra=0, min_num=1, validate_min=True,
    max_num=MAX_MANUAL_ROWS, validate_max=True, absolute_max=MAX_MANUAL_ROWS, can_delete=True,
)
