import csv
import hashlib
import io

from django.core.exceptions import ValidationError

from transactions.validation import validate_transaction


CSV_COLUMNS = ["occurred_at", "amount", "direction", "recipient"]
MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_CSV_ROWS = 10_000


def read_transactions_csv(file):
    # проверяет файл и собирает операции без записи в базу
    file.seek(0)
    content = file.read(MAX_CSV_BYTES + 1)
    if len(content) > MAX_CSV_BYTES:
        raise ValidationError("Поле file: размер CSV не должен превышать 2 МиБ")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValidationError("Поле file: сохраните CSV в кодировке UTF-8") from error

    reader = csv.reader(io.StringIO(text, newline=""), delimiter=";", strict=True)
    rows = []
    try:
        if next(reader, None) != CSV_COLUMNS:
            raise ValidationError(
                "Строка 1 поле заголовок: нужны occurred_at;amount;direction;recipient"
            )
        for row in reader:
            line = reader.line_num
            if not row:
                continue
            if len(rows) >= MAX_CSV_ROWS:
                raise ValidationError(f"Строка {line} поле file: максимум 10000 операций")
            if len(row) != len(CSV_COLUMNS):
                raise ValidationError(f"Строка {line} поле csv: ожидаются четыре значения")
            occurred_at, amount, direction, recipient = [value.strip() for value in row]

            try:
                rows.append(validate_transaction(occurred_at, amount, direction, recipient))
            except ValidationError as error:
                field, messages = next(iter(error.message_dict.items()))
                raise ValidationError(f"Строка {line} поле {field}: {messages[0]}") from error
    except csv.Error as error:
        raise ValidationError(
            f"Строка {reader.line_num} поле csv: неверная структура CSV"
        ) from error

    if not rows:
        raise ValidationError("Поле file: добавьте хотя бы одну операцию")
    return rows, hashlib.sha256(content).hexdigest()
