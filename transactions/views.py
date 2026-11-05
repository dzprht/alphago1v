from decimal import Decimal

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from transactions.forms import MAX_MANUAL_ROWS, ManualTransactionFormSet, TransactionUploadForm
from transactions.services import (
    delete_user_transactions,
    get_user_transactions,
    import_transactions,
    replace_transactions,
)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def history_view(request):
    # показывает свои операции и принимает новую историю
    action = request.POST.get("action", "csv") if request.method == "POST" else ""
    form = TransactionUploadForm(
        data=request.POST if action == "csv" else None,
        files=request.FILES if action == "csv" else None,
    )
    manual_data = request.POST if action in ("manual", "add_row") else None
    if action == "add_row":
        manual_data = request.POST.copy()
        submitted = ManualTransactionFormSet(manual_data, prefix="rows")
        if submitted.management_form.is_valid():
            count = min(max(submitted.total_form_count(), 0) + 1, MAX_MANUAL_ROWS)
            manual_data["rows-TOTAL_FORMS"] = str(count)
            manual_data.setdefault(f"rows-{count - 1}-direction", "outgoing")
    manual_forms = ManualTransactionFormSet(manual_data, prefix="rows")
    if action == "manual" and manual_forms.is_valid():
        rows = [item.cleaned_data["operation"] for item in manual_forms
                if item.cleaned_data and not item.cleaned_data.get("DELETE")]
        replace_transactions(request.user, rows)
        return redirect(reverse("transactions:history") + "?imported=1")
    if action == "csv" and form.is_valid():
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
            "manual_forms": manual_forms,
            "max_manual_rows": MAX_MANUAL_ROWS,
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
