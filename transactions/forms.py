from django import forms

from transactions.csv_import import MAX_CSV_BYTES


class TransactionUploadForm(forms.Form):
    file = forms.FileField(label="История операций в CSV")

    def clean_file(self):
        # проверяет размер выбранного файла
        file = self.cleaned_data["file"]
        if file.size > MAX_CSV_BYTES:
            raise forms.ValidationError("Размер CSV не должен превышать 2 МиБ")
        return file
