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
    path(
        "inventory/issues/new/",
        views.IssueCreateView.as_view(),
        name="issue-create",
    ),
    path(
        "inventory/issues/<uuid:pk>/",
        views.IssueDetailView.as_view(),
        name="issue-detail",
    ),
    path(
        "inventory/transactions/",
        views.TransactionHistoryListView.as_view(),
        name="transaction-history-list",
    ),
    path(
        "inventory/transactions/<uuid:pk>/",
        views.TransactionHistoryDetailView.as_view(),
        name="transaction-history-detail",
    ),
]
