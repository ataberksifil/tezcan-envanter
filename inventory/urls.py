from django.urls import path

from inventory import views

app_name = "inventory"

urlpatterns = [
    path(
        "production-lines/",
        views.ProductionLineListView.as_view(),
        name="production-line-list",
    ),
    path(
        "production-lines/new/",
        views.ProductionLineCreateView.as_view(),
        name="production-line-create",
    ),
    path(
        "production-lines/<uuid:pk>/",
        views.ProductionLineDetailView.as_view(),
        name="production-line-detail",
    ),
    path(
        "production-lines/<uuid:pk>/edit/",
        views.ProductionLineUpdateView.as_view(),
        name="production-line-update",
    ),
    path(
        "production-lines/<uuid:pk>/deactivate/",
        views.ProductionLineDeactivateView.as_view(),
        name="production-line-deactivate",
    ),
    path(
        "production-lines/<uuid:pk>/reactivate/",
        views.ProductionLineReactivateView.as_view(),
        name="production-line-reactivate",
    ),
]
