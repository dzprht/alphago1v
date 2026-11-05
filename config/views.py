from django.db import DatabaseError, connection
from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@never_cache
@require_GET
def ping_view(request):
    # отвечает что процесс приложения принимает запросы
    return HttpResponse("pong", content_type="text/plain")


@never_cache
@require_GET
def health_view(request):
    # проверяет ответ базы без запуска анализа
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
