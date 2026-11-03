"""Subtitle tracks: upload, edit in place, and serve as WebVTT."""
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.media import subtitles as subs
from apps.media.models import Media, Subtitle, SubtitleCue
from apps.rbac import permissions as policy


def _editable(user, media: Media) -> bool:
    if not user.is_authenticated:
        return False
    return media.owner_id == user.id or policy.can_curate_others(user)


def _media_for(user, uid: str) -> Media:
    media = get_object_or_404(Media, uid=uid)
    if not _editable(user, media):
        from django.http import Http404

        raise Http404("media not found")
    return media


def _normalise_language(raw: str) -> str:
    cleaned = (raw or "").strip().lower().replace("_", "-")
    return cleaned[:16] or "und"


@require_POST
def subtitle_upload(request, uid: str):
    media = _media_for(request.user, uid)
    upload = request.FILES.get("file")
    language = _normalise_language(request.POST.get("language", ""))

    if upload is None:
        messages.error(request, "Choose a .srt or .vtt file.")
        return redirect("core:media", uid=media.uid)

    raw = upload.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")

    cues = subs.parse(text)
    if not cues:
        messages.error(request, "No cues could be read from that file.")
        return redirect("core:media", uid=media.uid)

    with transaction.atomic():
        subtitle, created = Subtitle.objects.get_or_create(
            media=media,
            language=language,
            defaults={"label": (request.POST.get("label") or "")[:120]},
        )
        if not created:
            subtitle.cues.all().delete()
        if not subtitle.label:
            subtitle.label = (request.POST.get("label") or "")[:120]
        subtitle.save()

        SubtitleCue.objects.bulk_create(
            [
                SubtitleCue(
                    subtitle=subtitle,
                    position=index,
                    start_ms=cue["start_ms"],
                    end_ms=cue["end_ms"],
                    text=cue["text"],
                )
                for index, cue in enumerate(cues)
            ]
        )

    messages.success(request, f"{len(cues)} cues imported.")
    return redirect("core:subtitle_editor", uid=media.uid, language=subtitle.language)


def subtitle_editor(request, uid: str, language: str):
    media = _media_for(request.user, uid)
    subtitle = get_object_or_404(Subtitle, media=media, language=language)

    if request.method == "POST":
        return _save(request, media, subtitle)

    cues = subtitle.cues.order_by("position", "start_ms")
    return render(
        request,
        "core/subtitles.html",
        {"media": media, "subtitle": subtitle, "cues": cues},
    )


@require_POST
def _save(request, media: Media, subtitle: Subtitle):
    starts = request.POST.getlist("cue_start")
    ends = request.POST.getlist("cue_end")
    texts = request.POST.getlist("cue_text")

    if not (len(starts) == len(ends) == len(texts)):
        return HttpResponseBadRequest("cue fields are out of sync")

    rebuilt = []
    for index, (start, end, text) in enumerate(zip(starts, ends, texts)):
        try:
            start_ms = subs.parse_timestamp(start)
            end_ms = subs.parse_timestamp(end)
        except subs.SubtitleFormatError as exc:
            messages.error(request, f"Cue {index + 1}: {exc}")
            return redirect("core:subtitle_editor", uid=media.uid, language=subtitle.language)

        if end_ms < start_ms:
            messages.error(request, f"Cue {index + 1} ends before it starts.")
            return redirect("core:subtitle_editor", uid=media.uid, language=subtitle.language)

        rebuilt.append(
            SubtitleCue(
                subtitle=subtitle,
                position=index,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text.strip(),
            )
        )

    with transaction.atomic():
        subtitle.cues.all().delete()
        SubtitleCue.objects.bulk_create(rebuilt)

    messages.success(request, f"Saved {len(rebuilt)} cues.")
    return redirect("core:subtitle_editor", uid=media.uid, language=subtitle.language)


@require_POST
def subtitle_delete(request, uid: str, language: str):
    media = _media_for(request.user, uid)
    Subtitle.objects.filter(media=media, language=language).delete()
    messages.success(request, "Subtitle track removed.")
    return redirect("core:media", uid=media.uid)


def subtitle_vtt(request, uid: str, language: str):
    """The browser needs WebVTT; cues are stored structurally, so render on read."""
    media = get_object_or_404(Media, uid=uid)
    subtitle = get_object_or_404(Subtitle, media=media, language=language)

    if media.visibility != Media.Visibility.PUBLIC:
        if not _editable(request.user, media):
            from django.http import Http404

            raise Http404("subtitle not found")

    body = subs.to_vtt(
        [{"start_ms": c.start_ms, "end_ms": c.end_ms, "text": c.text}
         for c in subtitle.cues.order_by("position", "start_ms")]
    )
    return HttpResponse(body, content_type="text/vtt; charset=utf-8")
