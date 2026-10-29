from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Upper
from django.utils import timezone


class User(AbstractUser):
    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(Upper("username"), name="user_username_upper_unique"),
        ]


class LoginAttempt(models.Model):
    ip_address = models.GenericIPAddressField(unique=True)
    failures = models.PositiveIntegerField(default=0)
    window_started_at = models.DateTimeField(default=timezone.now)
