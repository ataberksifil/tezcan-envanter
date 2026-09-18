from django.urls import path

from counting import views

app_name = "counting"

urlpatterns = [
    path("", views.PhysicalCountSessionListView.as_view(), name="session-list"),
    path("new/", views.PhysicalCountSessionCreateView.as_view(), name="session-create"),
    path(
        "<uuid:pk>/",
        views.PhysicalCountSessionDetailView.as_view(),
        name="session-detail",
    ),
    path(
        "<uuid:pk>/start/",
        views.PhysicalCountSessionStartView.as_view(),
        name="session-start",
    ),
    path(
        "<uuid:pk>/quantity/",
        views.QuantityCountView.as_view(),
        name="quantity-count",
    ),
    path(
        "<uuid:pk>/quantity/unexpected/",
        views.UnexpectedQuantityView.as_view(),
        name="unexpected-quantity",
    ),
    path(
        "<uuid:pk>/quantity-lines/<uuid:line_pk>/not-counted/",
        views.QuantityNotCountedView.as_view(),
        name="quantity-not-counted",
    ),
    path(
        "<uuid:pk>/serialized/",
        views.SerializedCountView.as_view(),
        name="serialized-count",
    ),
    path(
        "<uuid:pk>/serialized/candidate/",
        views.CandidateSerializedView.as_view(),
        name="serialized-candidate",
    ),
    path(
        "<uuid:pk>/serialized-lines/<uuid:line_pk>/missing/",
        views.SerializedMissingView.as_view(),
        name="serialized-missing",
    ),
    path(
        "<uuid:pk>/serialized-lines/<uuid:line_pk>/not-counted/",
        views.SerializedNotCountedView.as_view(),
        name="serialized-not-counted",
    ),
    path(
        "<uuid:pk>/complete/",
        views.PhysicalCountCompleteView.as_view(),
        name="session-complete",
    ),
    path(
        "<uuid:pk>/discrepancies/",
        views.DiscrepancyReviewView.as_view(),
        name="discrepancy-review",
    ),
    path(
        "<uuid:pk>/quantity-lines/<uuid:line_pk>/approve/",
        views.QuantityDiscrepancyApproveView.as_view(),
        name="quantity-approve",
    ),
    path(
        "<uuid:pk>/quantity-lines/<uuid:line_pk>/reject/",
        views.QuantityDiscrepancyRejectView.as_view(),
        name="quantity-reject",
    ),
]
