from django.urls import path

from imports import views

app_name = "imports"

urlpatterns = [
    path("baselines/", views.InventoryBaselineListView.as_view(), name="baseline-list"),
    path(
        "baselines/new/",
        views.InventoryBaselineCreateView.as_view(),
        name="baseline-create",
    ),
    path(
        "baselines/<uuid:pk>/",
        views.InventoryBaselineDetailView.as_view(),
        name="baseline-detail",
    ),
    path(
        "baselines/<uuid:pk>/establish/",
        views.InventoryBaselineEstablishView.as_view(),
        name="baseline-establish",
    ),
]
