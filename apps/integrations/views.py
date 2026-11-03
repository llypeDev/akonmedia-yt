"""Integrations: the frame's sign-in, the embed script and the studio pages."""
import json
import time

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.core.templatetags.akon import avatar
from apps.live.models import Live
from apps.rbac import permissions as policy

from . import identities, sessions, tokens
from .models import Integration, normalize_origin

#: Sign-ins one address may attempt per minute.
SESSION_BURST = 30


def _origin_of(request) -> str:
    return normalize_origin(request.build_absolute_uri("/"))


# ---------------------------------------------------------------- the frame


@csrf_exempt
@require_POST
def session(request):
    """Trade a site's token for a session of the embed frame.

    Body (JSON): {"token": "<the site's token>", "origin": "<the site's page origin>"}
    """
    address = request.META.get("REMOTE_ADDR", "")
    key = f"integrations:session:{address}"
    cache.add(key, 0, timeout=60)
    try:
        used = cache.incr(key)
    except ValueError:
        used = 1
    if used > SESSION_BURST:
        return JsonResponse({"detail": _("Too many sign-ins: wait a minute."), "code": "rate"}, status=429)

    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        person = tokens.verify(str(data.get("token") or ""))
    except tokens.TokenError as error:
        return JsonResponse({"detail": str(error), "code": "token"}, status=401)

    integration = person.integration
    origin = normalize_origin(str(data.get("origin") or ""))
    if integration.origin_list and origin != _origin_of(request) and not integration.allows_origin(origin):
        return JsonResponse(
            {
                "detail": _("This site is not allowed to use the integration: add %(origin)s to its sites.")
                % {"origin": origin or "?"},
                "code": "origin",
            },
            status=403,
        )

    identity = identities.sync(person)
    if not identity.user.is_active:
        return JsonResponse({"detail": _("This account is blocked."), "code": "blocked"}, status=403)
    Integration.objects.filter(pk=integration.pk).update(last_used_at=timezone.now())
    return JsonResponse(
        {
            "session": sessions.issue(identity),
            "expires_in": sessions.LIFETIME,
            "user": {
                "name": identity.user.get_short_name(),
                "avatar": str(avatar(identity.user, "sm")),
                "moderator": identity.is_moderator,
            },
        }
    )


@cache_control(max_age=3600, public=True)
def sdk(request):
    """The script a site adds to its pages (see templates/integrations/sdk.js)."""
    return render(
        request,
        "integrations/sdk.js",
        {"origin": _origin_of(request)},
        content_type="application/javascript; charset=utf-8",
    )


# ----------------------------------------------------------------- studio


class IntegrationForm(forms.ModelForm):
    class Meta:
        model = Integration
        fields = ["name", "origins"]
        widgets = {
            "name": forms.TextInput(attrs={"maxlength": 120, "autocomplete": "off"}),
            "origins": forms.Textarea(attrs={"rows": 3, "spellcheck": "false"}),
        }
        labels = {"name": gettext_lazy("Name"), "origins": gettext_lazy("Sites")}


def _can_integrate(user) -> bool:
    return policy.can_upload(user) and not hasattr(user, "external_identity")


def _mine(request, pk) -> Integration:
    integration = get_object_or_404(Integration, pk=pk)
    if integration.owner_id != request.user.id and not policy.can_configure_portal(request.user):
        raise Http404("integration not found")
    return integration


@login_required
def studio_list(request):
    if not _can_integrate(request.user):
        raise PermissionDenied
    form = IntegrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        integration = form.save(commit=False)
        integration.owner = request.user
        integration.save()
        messages.success(request, _("Integration created. Follow the steps below to put your lives on your site."))
        return redirect("integrations:detail", pk=integration.pk)
    integrations = Integration.objects.filter(owner=request.user).order_by("-created_at")
    return render(request, "integrations/list.html", {"integrations": integrations, "form": form})


@login_required
def studio_detail(request, pk):
    integration = _mine(request, pk)
    form = IntegrationForm(request.POST or None, instance=integration)
    if request.method == "POST":
        if "is_active" in request.POST:
            integration.is_active = request.POST.get("is_active") == "1"
            integration.save(update_fields=["is_active"])
            messages.success(
                request, _("Integration turned on.") if integration.is_active else _("Integration turned off.")
            )
            return redirect("integrations:detail", pk=integration.pk)
        if form.is_valid():
            form.save()
            messages.success(request, _("Integration saved."))
            return redirect("integrations:detail", pk=integration.pk)

    lives = list(
        Live.objects.filter(owner=integration.owner).order_by("-created_at").only("uid", "title", "status", "visibility")[:50]
    )
    chosen = request.GET.get("live") or (lives[0].uid if lives else "")
    layout = request.GET.get("layout", "both")
    layout = layout if layout in ("both", "player", "chat") else "both"
    theme = request.GET.get("theme", "")
    theme = theme if theme in ("dark", "light") else ""
    people = integration.identities.select_related("user").order_by("-last_seen_at")[:20]
    return render(
        request,
        "integrations/detail.html",
        {
            "integration": integration,
            "form": form,
            "lives": lives,
            "chosen": chosen,
            "layout": layout,
            "theme": theme,
            "people": people,
            "people_count": integration.identities.count(),
            "origin": _origin_of(request),
            "sdk_url": request.build_absolute_uri(reverse("integrations:sdk")),
        },
    )


@login_required
@require_POST
def rotate(request, pk):
    integration = _mine(request, pk)
    integration.rotate_secret()
    messages.success(request, _("New secret created. Update it on your site's server: the old one no longer works."))
    return redirect("integrations:detail", pk=integration.pk)


@login_required
@require_POST
def delete(request, pk):
    integration = _mine(request, pk)
    integration.delete()
    messages.success(request, _("Integration deleted."))
    return redirect("integrations:list")


@login_required
def preview(request, pk):
    """The integration as a site would show it, signed in as a test student.

    The token is made here with the integration's secret, the way the site's
    server would make it; the page then loads the embed script like any site.
    """
    integration = _mine(request, pk)
    live = get_object_or_404(Live, uid=request.GET.get("live", ""), owner=integration.owner)
    layout = request.GET.get("layout", "both")
    layout = layout if layout in ("both", "player", "chat") else "both"
    theme = request.GET.get("theme", "")
    theme = theme if theme in ("dark", "light") else ""
    name = (request.GET.get("name") or "").strip()[:60] or _("Test student")
    moderator = request.GET.get("moderator") == "1"
    now = int(time.time())
    token = tokens.sign(
        {
            "sub": f"preview-{request.user.pk}-{'teacher' if moderator else 'student'}",
            "name": name,
            "moderator": moderator,
            "iat": now,
            "exp": now + 600,
        },
        integration,
    )
    return render(
        request,
        "integrations/preview.html",
        {
            "integration": integration,
            "live": live,
            "layout": layout,
            "theme": theme,
            "name": name,
            "moderator": moderator,
            "token": token,
            "sdk_url": reverse("integrations:sdk"),
        },
    )
