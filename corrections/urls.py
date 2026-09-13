from django.urls import path

from corrections import views

app_name = "corrections"

urlpatterns = [
    path("", views.CorrectionRequestListView.as_view(), name="request-list"),
    path("<uuid:pk>/", views.CorrectionRequestDetailView.as_view(), name="request-detail"),
    path(
        "for-transaction/<uuid:transaction_pk>/new/",
        views.CorrectionRequestCreateView.as_view(),
        name="request-create",
    ),
    path("<uuid:pk>/approve/", views.CorrectionApproveView.as_view(), name="approve"),
    path("<uuid:pk>/reject/", views.CorrectionRejectView.as_view(), name="reject"),
]
