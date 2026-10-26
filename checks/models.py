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
