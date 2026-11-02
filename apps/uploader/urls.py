from django.urls import path

from .views import ChunkUploadView, CompleteUploadView, StartUploadView

app_name = "uploader"

urlpatterns = [
    path("uploads/", StartUploadView.as_view(), name="start"),
    path("uploads/<uuid:session_id>/chunk/", ChunkUploadView.as_view(), name="chunk"),
    path("uploads/<uuid:session_id>/complete/", CompleteUploadView.as_view(), name="complete"),
]
