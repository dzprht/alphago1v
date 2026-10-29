from datetime import timedelta
from math import ceil

from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.forms import RegistrationForm
from accounts.models import LoginAttempt
from accounts.services import login_user


LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW = timedelta(minutes=15)


@sensitive_post_parameters("password1", "password2")
@never_cache
@require_http_methods(["GET", "POST"])
def register_view(request):
    # проверяет регистрацию и создает аккаунт
    if request.user.is_authenticated:
        return redirect("accounts:profile")
    form = RegistrationForm(data=request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError as error:
            constraint = error.__cause__.diag.constraint_name
            if constraint not in {"user_username_upper_unique", "accounts_user_username_key"}:
                raise
            form.add_error("username", "Этот логин уже занят")
        else:
            return redirect("accounts:login")
    return render(request, "accounts/register.html", {"form": form})


@sensitive_post_parameters("password")
@never_cache
@require_http_methods(["GET", "POST"])
def login_view(request):
    # выполняет вход и ограничивает неверные попытки
    if request.user.is_authenticated:
        return redirect("accounts:profile")
    if request.method == "GET":
        return render(
            request, "accounts/login.html", {"form": AuthenticationForm(request=request)}
        )

    retry_after = 0
    with transaction.atomic():
        attempt, _ = LoginAttempt.objects.select_for_update().get_or_create(
            ip_address=request.META.get("REMOTE_ADDR", "0.0.0.0")
        )
        now = timezone.now()
        if now >= attempt.window_started_at + LOGIN_FAILURE_WINDOW:
            attempt.failures = 0
        if attempt.failures >= LOGIN_FAILURE_LIMIT:
            remaining = attempt.window_started_at + LOGIN_FAILURE_WINDOW - now
            retry_after = max(1, ceil(remaining.total_seconds()))
        else:
            form = login_user(request, request.POST)
            if not form.is_valid():
                if attempt.failures == 0:
                    attempt.window_started_at = now
                attempt.failures += 1
            attempt.save(update_fields=["failures", "window_started_at"])

    if retry_after:
        response = render(
            request,
            "accounts/login.html",
            {
                "form": AuthenticationForm(request=request),
                "error": "Слишком много попыток входа с этого IP Попробуйте позже",
            },
            status=429,
        )
        response["Retry-After"] = str(retry_after)
        return response
    if form.is_valid():
        return redirect("accounts:profile")
    return render(request, "accounts/login.html", {"form": form})


@never_cache
@require_POST
def logout_view(request):
    # завершает текущую сессию и возвращает на вход
    logout(request)
    return redirect("accounts:login")


@never_cache
@require_GET
@login_required
def account_view(request):
    # показывает какой аккаунт сейчас вошел
    return render(request, "accounts/profile.html")
