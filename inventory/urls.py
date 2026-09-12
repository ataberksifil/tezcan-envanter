from django.urls import path

from inventory import views

app_name = "inventory"

urlpatterns = [
    path(
        "management/production-lines/",
        views.ProductionLineListView.as_view(),
        name="production-line-list",
    ),
    path(
        "management/production-lines/new/",
        views.ProductionLineCreateView.as_view(),
        name="production-line-create",
    ),
    path(
        "management/production-lines/<uuid:pk>/",
        views.ProductionLineDetailView.as_view(),
        name="production-line-detail",
    ),
    path(
        "management/production-lines/<uuid:pk>/edit/",
        views.ProductionLineUpdateView.as_view(),
        name="production-line-update",
    ),
    path(
        "management/production-lines/<uuid:pk>/deactivate/",
        views.ProductionLineDeactivateView.as_view(),
        name="production-line-deactivate",
    ),
    path(
        "management/production-lines/<uuid:pk>/reactivate/",
        views.ProductionLineReactivateView.as_view(),
        name="production-line-reactivate",
    ),
    path(
        "inventory/receipts/new/",
        views.ReceiptCreateView.as_view(),
        name="receipt-create",
    ),
    path(
        "inventory/receipts/<uuid:pk>/",
        views.ReceiptDetailView.as_view(),
        name="receipt-detail",
    ),
]
