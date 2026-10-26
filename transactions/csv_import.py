import csv
import hashlib
import io
import re
from datetime import datetime, timezone
from decimal import Decimal

from django.core.exceptions import ValidationError

from transactions.models import Transaction


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
                occurred_at = datetime.fromisoformat(occurred_at)
                if occurred_at.utcoffset() is None:
                    raise ValueError
                occurred_at = occurred_at.astimezone(timezone.utc)
            except (ValueError, OverflowError) as error:
                raise ValidationError(
                    f"Строка {line} поле occurred_at: нужна дата ISO 8601 с часовым поясом"
                ) from error

            if not re.fullmatch(r"[0-9]{1,16}([.,][0-9]{1,2})?", amount):
                raise ValidationError(
                    f"Строка {line} поле amount: нужна сумма до 16 цифр и двух знаков копеек"
                )
            amount_kopecks = int(Decimal(amount.replace(",", ".")) * 100)
            if amount_kopecks <= 0:
                raise ValidationError(f"Строка {line} поле amount: сумма должна быть больше нуля")
            if direction not in Transaction.Direction.values:
                raise ValidationError(
                    f"Строка {line} поле direction: допустимы incoming и outgoing"
                )
            if len(recipient) > 100 or any(ord(char) < 32 for char in recipient):
                raise ValidationError(
                    f"Строка {line} поле recipient: максимум 100 символов "
                    "без переносов и управляющих знаков"
                )
            rows.append(
                {
                    "occurred_at": occurred_at,
                    "amount_kopecks": amount_kopecks,
                    "direction": direction,
                    "recipient": recipient,
                }
            )
    except csv.Error as error:
        raise ValidationError(
            f"Строка {reader.line_num} поле csv: неверная структура CSV"
        ) from error

    if not rows:
        raise ValidationError("Поле file: добавьте хотя бы одну операцию")
    return rows, hashlib.sha256(content).hexdigest()
