"""Subtitle tracks: upload, edit in place, and serve as WebVTT or SubRip."""
from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.media import access
from apps.media import subtitles as subs
from apps.media.models import Media, Subtitle, SubtitleCue

MAX_SUBTITLE_BYTES = 2 * 1024 * 1024


def _editable(user, media: Media) -> bool:
    return access.can_edit_media(user, media)


def _media_for(user, uid: str) -> Media:
    media = get_object_or_404(Media, uid=uid)
    if not _editable(user, media):
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
    back = request.POST.get("next") == "edit"

    def bounce():
        return redirect("core:media_edit" if back else "core:media", uid=media.uid)

    if upload is None:
        messages.error(request, "Choose a .srt or .vtt file.")
        return bounce()
    if upload.size > MAX_SUBTITLE_BYTES:
        messages.error(request, "Subtitle files must be 2 MB or smaller.")
        return bounce()

    raw = upload.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")

    cues = subs.parse(text)
    if not cues:
        messages.error(request, "No cues could be read from that file.")
        return bounce()

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
        if not start.strip() and not end.strip() and not text.strip():
            continue  # a blank row added in the editor and never filled in
        try:
            start_ms = subs.parse_timestamp(start)
            end_ms = subs.parse_timestamp(end)
        except (subs.SubtitleFormatError, ValueError) as exc:
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

    rebuilt.sort(key=lambda cue: (cue.start_ms, cue.position))
    for position, cue in enumerate(rebuilt):
        cue.position = position

    label = request.POST.get("label")
    with transaction.atomic():
        if label is not None:
            subtitle.label = label.strip()[:120]
            subtitle.save(update_fields=["label", "updated_at"])
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


def _readable_track(request, uid: str, language: str):
    media = get_object_or_404(Media, uid=uid)
    if not access.can_view_media(request.user, media):
        raise Http404("subtitle not found")
    subtitle = get_object_or_404(Subtitle, media=media, language=language)
    cues = [
        {"start_ms": c.start_ms, "end_ms": c.end_ms, "text": c.text}
        for c in subtitle.cues.order_by("position", "start_ms")
    ]
    return media, subtitle, cues


def subtitle_vtt(request, uid: str, language: str):
    """The browser needs WebVTT; cues are stored structurally, so render on read."""
    _, _, cues = _readable_track(request, uid, language)
    return HttpResponse(subs.to_vtt(cues), content_type="text/vtt; charset=utf-8")


def subtitle_srt(request, uid: str, language: str):
    media, subtitle, cues = _readable_track(request, uid, language)
    response = HttpResponse(subs.to_srt(cues), content_type="application/x-subrip; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{media.uid}.{subtitle.language}.srt"'
    return response
