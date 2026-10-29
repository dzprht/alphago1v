from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    pass


class LoginAttempt(models.Model):
    ip_address = models.GenericIPAddressField(unique=True)
    failures = models.PositiveIntegerField(default=0)
    window_started_at = models.DateTimeField(default=timezone.now)
