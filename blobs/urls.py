from django.urls import path

from . import views

app_name = "blobs"

urlpatterns = [
    path("", views.feed, name="feed"),
    path("b/<int:pk>/", views.detail, name="detail"),
    path("b/<int:pk>/edit/", views.edit, name="edit"),
    path("b/<int:pk>/delete/", views.delete, name="delete"),
]
