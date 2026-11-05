import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from analysis.runtime import load_analyzer, load_media_model
from analysis.url import CATALOGUE_PATH
from checks.forms import (
    AudioCheckForm, CheckFeedbackForm, ScreenshotCheckForm, TextCheckForm,
    TrainingConsentForm, TransferCheckForm, URLCheckForm,
)
from checks.models import Check
from checks.services import (
    consent_is_current,
    get_user_check,
    get_user_checks,
    run_check,
    save_check_feedback,
    save_training_consent,
)


logger = logging.getLogger(__name__)
CHECK_FORMS = {
    form.kind: form for form in (
        TextCheckForm, URLCheckForm, AudioCheckForm, ScreenshotCheckForm, TransferCheckForm
    )
}


@require_GET
def home_view(request):
    # показывает доступные виды проверок
    return render(request, "checks/home.html", {"kinds": Check.Kind.choices})


@never_cache
@require_http_methods(["GET", "POST"])
def input_view(request, kind):
    # проверяет ввод и показывает результат анализа
    form_class = CHECK_FORMS.get(kind)
    if form_class is None:
        raise Http404
    form, status = form_class(), 200
    unavailable = not settings.ANALYZER_DIR
    if request.method == "POST":
        form = form_class(request.POST, request.FILES)
        status = 400
        if form.is_valid():
            status = 503
            media_model = None
            try:
                analyzer = load_analyzer(settings.ANALYZER_DIR)
                if analyzer is not None and form.is_multipart():
                    media_model = load_media_model(kind, settings.ANALYZER_DIR)
            except Exception:
                logger.exception("Не удалось загрузить анализатор")
                analyzer = None
            if analyzer is not None and (not form.is_multipart() or media_model is not None):
                field = "audio" if kind == Check.Kind.AUDIO else "image"
                upload = form.cleaned_data[field] if form.is_multipart() else None
                check = run_check(request.user, kind, form.get_input_data(), analyzer,
                                  upload=upload, media_model=media_model)
                if request.user.is_authenticated:
                    return redirect("checks:detail", check_id=check.pk)
                return render(request, "checks/guest_result.html", {"check": check},
                              status=200 if check.status == Check.Status.COMPLETED else 503)
            unavailable = True
    organizations = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))["brands"] if kind == Check.Kind.URL else []
    return render(request, "checks/input.html", {
        "form": form, "unavailable": unavailable, "organizations": organizations,
    }, status=status)


@never_cache
@require_GET
@login_required
def history_view(request):
    # показывает только свою историю проверок
    return render(
        request, "checks/history.html", {"checks": get_user_checks(request.user)}
    )


@never_cache
@require_http_methods(["GET", "POST"])
@login_required
def detail_view(request, check_id):
    # показывает сохраненный результат и принимает отзыв или согласие
    check = get_user_check(request.user, check_id)
    feedback_form = None
    consent_form = None

    if request.method == "POST":
        if check.status != Check.Status.COMPLETED:
            return HttpResponseBadRequest("Для отзыва или согласия нужен готовый результат")
        action = request.POST.get("action")
        if action == "feedback":
            feedback_form = save_check_feedback(request.user, check_id, request.POST)
            if feedback_form.is_valid():
                return redirect("checks:detail", check_id=check_id)
        elif action == "consent":
            consent_form = save_training_consent(request.user, check_id, request.POST)
            if consent_form.is_valid():
                return redirect("checks:detail", check_id=check_id)
        else:
            return HttpResponseBadRequest("Неизвестное действие")

    feedback = getattr(check, "feedback", None)
    training_allowed = consent_is_current(getattr(check, "training_consent", None))
    if feedback_form is None:
        feedback_form = CheckFeedbackForm(
            initial={"outcome": feedback.outcome, "comment": feedback.comment} if feedback else None
        )
    if consent_form is None:
        consent_form = TrainingConsentForm(initial={"granted": training_allowed})

    return render(
        request,
        "checks/detail.html",
        {
            "check": check,
            "feedback": feedback,
            "feedback_form": feedback_form,
            "consent_form": consent_form,
            "training_allowed": training_allowed,
            "input_text": json.dumps(check.input_data, ensure_ascii=False, indent=2),
            "result_text": json.dumps(check.result, ensure_ascii=False, indent=2),
        },
    )
