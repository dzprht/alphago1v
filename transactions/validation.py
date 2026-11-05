import re
from datetime import datetime, timezone
from decimal import Decimal

from django.core.exceptions import ValidationError

from transactions.models import Transaction


def validate_transaction(occurred_at, amount, direction, recipient):
    # проверяет одну операцию для файла и ручного ввода
    try:
        if not isinstance(occurred_at, datetime):
            occurred_at = datetime.fromisoformat(occurred_at)
        if occurred_at.utcoffset() is None:
            raise ValueError
        occurred_at = occurred_at.astimezone(timezone.utc)
    except (ValueError, OverflowError) as error:
        raise ValidationError({"occurred_at": "нужна дата ISO 8601 с часовым поясом"}) from error
    if not re.fullmatch(r"[0-9]{1,16}([.,][0-9]{1,2})?", amount):
        raise ValidationError({"amount": "нужна сумма до 16 цифр и двух знаков копеек"})
    amount_kopecks = int(Decimal(amount.replace(",", ".")) * 100)
    if amount_kopecks <= 0:
        raise ValidationError({"amount": "сумма должна быть больше нуля"})
    if direction not in Transaction.Direction.values:
        raise ValidationError({"direction": "допустимы incoming и outgoing"})
    if len(recipient) > 100 or any(ord(char) < 32 for char in recipient):
        raise ValidationError({"recipient": "максимум 100 символов без переносов и управляющих знаков"})
    return {"occurred_at": occurred_at, "amount_kopecks": amount_kopecks,
            "direction": direction, "recipient": recipient}
