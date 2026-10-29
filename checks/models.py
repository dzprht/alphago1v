from django.conf import settings
from django.db import models


class Check(models.Model):
    class Kind(models.TextChoices):
        TEXT = "text", "Текст"
        URL = "url", "Ссылка"
        AUDIO = "audio", "Аудио"
        SCREENSHOT = "screenshot", "Скриншот"
        TRANSFER = "transfer", "Перевод"

    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает обработки"
        COMPLETED = "completed", "Завершена"
        FAILED = "failed", "Ошибка обработки"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="checks"
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    input_data = models.JSONField(default=dict)
    extracted_text = models.TextField(blank=True)
    result = models.JSONField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    context_snapshot = models.JSONField(default=dict, blank=True)
    analyzer_version = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    kind__in=["text", "url", "audio", "screenshot", "transfer"]
                ),
                name="check_kind_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["pending", "completed", "failed"]),
                name="check_status_valid",
            ),
        ]


class CheckFeedback(models.Model):
    class Outcome(models.TextChoices):
        FRAUD = "fraud", "Мошенничество"
        ORDINARY = "ordinary", "Обычная ситуация"
        UNKNOWN = "unknown", "Неизвестно"

    check_record = models.OneToOneField(
        Check, on_delete=models.CASCADE, related_name="feedback"
    )
    outcome = models.CharField(max_length=8, choices=Outcome.choices)
    comment = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(outcome__in=["fraud", "ordinary", "unknown"]),
                name="feedback_outcome_valid",
            ),
        ]


class TrainingConsent(models.Model):
    check_record = models.OneToOneField(
        Check, on_delete=models.CASCADE, related_name="training_consent"
    )
    granted = models.BooleanField(default=False)
    terms_version = models.CharField(max_length=20)
    updated_at = models.DateTimeField(auto_now=True)
