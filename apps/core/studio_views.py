"""The creator's side: studio dashboard, media editing, account settings."""
import re
import secrets

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.media import access, pipeline
from apps.media.models import Category, Chapter, Comment, Media, Playlist

STUDIO_PAGE_SIZE = 20
MAX_POSTER_BYTES = 10 * 1024 * 1024


class MediaDetailsForm(forms.ModelForm):
    class Meta:
        model = Media
        fields = ["title", "description", "visibility", "category"]
        widgets = {"description": forms.Textarea(attrs={"rows": 6})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.order_by("name")
        self.fields["category"].empty_label = "No category"
        self.fields["title"].widget.attrs.update({"maxlength": 255, "autocomplete": "off"})


@login_required
def studio(request):
    mine = Media.objects.filter(owner=request.user)

    totals = mine.aggregate(
        items=Count("id"),
        views=Sum("views_count"),
        size=Sum("size_bytes"),
        seconds=Sum("duration_seconds"),
    )
    by_state = dict(mine.values_list("state").annotate(n=Count("id")).values_list("state", "n"))
    totals["comments"] = Comment.objects.filter(media__owner=request.user, is_hidden=False).count()
    totals["playlists"] = Playlist.objects.filter(owner=request.user).count()
    totals["hours"] = round((totals["seconds"] or 0) / 3600, 2)

    state = request.GET.get("state", "")
    kind = request.GET.get("kind", "")
    query = (request.GET.get("q") or "").strip()

    listing = mine.select_related("category").annotate(
        comment_count=Count("comments", filter=Q(comments__is_hidden=False))
    )
    if state in Media.State.values:
        listing = listing.filter(state=state)
    else:
        state = ""
    if kind in Media.Kind.values:
        listing = listing.filter(kind=kind)
    else:
        kind = ""
    if query:
        listing = listing.filter(Q(title__icontains=query) | Q(description__icontains=query))

    page_obj = Paginator(listing.order_by("-created_at"), STUDIO_PAGE_SIZE).get_page(
        request.GET.get("page")
    )
    return render(
        request,
        "core/studio.html",
        {
            "totals": totals,
            "by_state": by_state,
            "page_obj": page_obj,
            "state": state,
            "kind": kind,
            "query": query,
            "states": Media.State.choices,
            "kinds": Media.Kind.choices,
            "processing": by_state.get("pending", 0) + by_state.get("processing", 0),
        },
    )


def _editable_media(request, uid: str) -> Media:
    media = get_object_or_404(Media.objects.select_related("owner", "category"), uid=uid)
    if not access.can_edit_media(request.user, media):
        raise Http404("media not found")
    return media


CLOCK = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:[.,]\d+)?)\s*$")


def parse_clock(raw: str) -> float | None:
    """'90', '1:30', '01:01:30' or '1:30.5' to seconds."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw.replace(",", ".")))
    except ValueError:
        pass
    match = CLOCK.match(raw)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + float(seconds.replace(",", "."))


@login_required
def media_edit(request, uid: str):
    media = _editable_media(request, uid)
    form = MediaDetailsForm(instance=media)

    if request.method == "POST":
        action = request.POST.get("action", "details")
        if action == "details":
            form = MediaDetailsForm(request.POST, instance=media)
            if form.is_valid():
                form.save()
                messages.success(request, "Details saved.")
                return redirect("core:media_edit", uid=media.uid)
            messages.error(request, "Please fix the highlighted fields.")
        elif action == "poster":
            _replace_poster(request, media)
            return redirect(f"{reverse('core:media_edit', args=[media.uid])}#thumbnail")
        elif action == "chapters":
            _save_chapters(request, media)
            return redirect(f"{reverse('core:media_edit', args=[media.uid])}#chapters")

    return render(
        request,
        "core/media_edit.html",
        {
            "media": media,
            "form": form,
            "chapters": media.chapters.all(),
            "subtitles": media.subtitles.all(),
            "is_owner": media.owner_id == request.user.id,
        },
    )


def _replace_poster(request, media: Media) -> None:
    upload = request.FILES.get("poster")
    if upload is None:
        messages.error(request, "Choose an image for the thumbnail.")
        return
    if upload.size > MAX_POSTER_BYTES:
        messages.error(request, "Thumbnails must be 10 MB or smaller.")
        return

    from pathlib import Path

    from django.conf import settings

    root = Path(settings.MEDIA_ROOT).resolve()
    folder = media.media_directory()
    if folder is None:
        folder = root / "media" / f"{timezone.now():%Y/%m}" / media.uid
    target = folder / f"poster-{secrets.token_hex(4)}.jpg"
    try:
        pipeline.image_poster(upload, target)
    except pipeline.PipelineError:
        messages.error(request, "That file is not an image we can read.")
        return

    previous = media.poster.name if media.poster else ""
    media.poster.name = str(target.relative_to(root)).replace("\\", "/")
    media.save(update_fields=["poster", "updated_at"])
    # Earlier custom thumbnails are dropped; the encoder's own poster.jpg stays
    # so that re-processing still has something to fall back on.
    if previous and previous.rsplit("/", 1)[-1].startswith("poster-"):
        media.poster.storage.delete(previous)
    messages.success(request, "Thumbnail updated.")


def _save_chapters(request, media: Media) -> None:
    starts = request.POST.getlist("chapter_start")
    titles = request.POST.getlist("chapter_title")
    if len(starts) != len(titles):
        messages.error(request, "Chapter fields are out of sync.")
        return

    rows = []
    for number, (raw_start, title) in enumerate(zip(starts, titles), start=1):
        title = title.strip()
        if not raw_start.strip() and not title:
            continue  # an empty row the browser left behind
        seconds = parse_clock(raw_start)
        if seconds is None or not title:
            messages.error(request, f"Chapter {number} needs a start time (like 1:30) and a title.")
            return
        if media.duration_seconds and seconds > media.duration_seconds:
            messages.error(request, f"Chapter {number} starts after the end of the media.")
            return
        rows.append((seconds, title[:255]))

    rows.sort()
    media.chapters.all().delete()
    Chapter.objects.bulk_create(
        Chapter(media=media, start_seconds=seconds, title=title, position=index)
        for index, (seconds, title) in enumerate(rows)
    )
    messages.success(request, f"Saved {len(rows)} chapter{'s' if len(rows) != 1 else ''}.")


@login_required
@require_POST
def media_reprocess(request, uid: str):
    """Run the encoder again, e.g. after a failure caused by a missing tool."""
    media = _editable_media(request, uid)
    if media.state == Media.State.PROCESSING:
        messages.info(request, "This item is already being processed.")
        return redirect("core:media", uid=media.uid)
    media.state = Media.State.PENDING
    media.failure_reason = ""
    media.save(update_fields=["state", "failure_reason", "updated_at"])
    from apps.media.tasks import process_media

    process_media.delay(str(media.pk))
    messages.success(request, "Processing restarted.")
    return redirect("core:media", uid=media.uid)


@login_required
@require_POST
def media_delete(request, uid: str):
    media = _editable_media(request, uid)
    title = media.title
    owner_is_me = media.owner_id == request.user.id
    media.delete()
    messages.success(request, f"“{title}” was deleted.")
    return redirect("core:studio" if owner_is_me else "core:manage")


@login_required
def account(request):
    user = request.user
    if request.method == "POST":
        name = (request.POST.get("display_name") or "").strip()[:120]
        user.display_name = name
        user.save(update_fields=["display_name"])
        messages.success(request, "Profile updated.")
        return redirect("core:account")

    stats = Media.objects.filter(owner=user).aggregate(items=Count("id"), views=Sum("views_count"))
    return render(request, "core/account.html", {"stats": stats})
