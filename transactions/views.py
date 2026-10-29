from decimal import Decimal

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from transactions.forms import TransactionUploadForm
from transactions.services import (
    delete_user_transactions,
    get_user_transactions,
    import_transactions,
)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def history_view(request):
    # показывает свои операции и принимает новую историю
    form = TransactionUploadForm(
        data=request.POST if request.method == "POST" else None,
        files=request.FILES if request.method == "POST" else None,
    )
    if request.method == "POST" and form.is_valid():
        try:
            import_transactions(request.user, form.cleaned_data["file"])
        except ValidationError as error:
            form.add_error("file", error)
        else:
            return redirect(reverse("transactions:history") + "?imported=1")

    transactions = get_user_transactions(request.user)
    for operation in transactions:
        operation["amount_rubles"] = Decimal(operation["amount_kopecks"]) / 100
    return render(
        request,
        "transactions/history.html",
        {
            "form": form,
            "transactions": transactions,
            "imported": request.GET.get("imported") == "1",
            "deleted": request.GET.get("deleted") == "1",
        },
    )


@never_cache
@login_required
@require_POST
def delete_view(request):
    # удаляет свою загруженную историю
    delete_user_transactions(request.user)
    return redirect(reverse("transactions:history") + "?deleted=1")


@never_cache
@login_required
@require_GET
def template_view(request):
    # отправляет готовый пример истории файлом
    path = settings.BASE_DIR / "examples" / "transactions.csv"
    return FileResponse(
        path.open("rb"),
        as_attachment=True,
        filename="transactions.csv",
        content_type="text/csv; charset=utf-8",
    )
