"""Public pages and operational endpoints."""
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache


def index(request):
    return render(request, "core/index.html")


@never_cache
def health(request):
    """Liveness probe. Deliberately dumb: it must answer even when the
    database is down, so orchestration can tell "starting" from "crashed"."""
    return JsonResponse({"status": "ok", "service": "akonmedia"})
