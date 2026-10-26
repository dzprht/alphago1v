from django.conf import settings
from django.db import models


class TransactionImport(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="transaction_import",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    row_count = models.PositiveIntegerField()
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    file_sha256 = models.CharField(max_length=64)


class Transaction(models.Model):
    class Direction(models.TextChoices):
        INCOMING = "incoming", "Поступление"
        OUTGOING = "outgoing", "Списание"

    history = models.ForeignKey(
        TransactionImport, on_delete=models.CASCADE, related_name="transactions"
    )
    occurred_at = models.DateTimeField()
    amount_kopecks = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3, default="RUB", editable=False)
    direction = models.CharField(max_length=8, choices=Direction.choices)
    recipient = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount_kopecks__gt=0, amount_kopecks__lt=10**18),
                name="transaction_amount_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(direction__in=["incoming", "outgoing"]),
                name="transaction_direction_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(currency="RUB"), name="transaction_currency_rub"
            ),
        ]
