from django.urls import path

from procurement import views

app_name = "procurement"

urlpatterns = [
    path("", views.PurchaseRequestListView.as_view(), name="request-list"),
    path("new/", views.PurchaseRequestCreateView.as_view(), name="request-create"),
    path("<uuid:pk>/", views.PurchaseRequestDetailView.as_view(), name="request-detail"),
    path("<uuid:pk>/edit/", views.PurchaseRequestUpdateView.as_view(), name="request-edit"),
    path(
        "<uuid:pk>/lines/new/",
        views.PurchaseRequestLineCreateView.as_view(),
        name="line-create",
    ),
    path(
        "<uuid:pk>/lines/<uuid:line_pk>/edit/",
        views.PurchaseRequestLineUpdateView.as_view(),
        name="line-edit",
    ),
    path(
        "<uuid:pk>/lines/<uuid:line_pk>/link/",
        views.PurchaseRequestLineLinkView.as_view(),
        name="line-link",
    ),
    path(
        "<uuid:pk>/lines/<uuid:line_pk>/receive/",
        views.PurchaseRequestLineReceiveView.as_view(),
        name="line-receive",
    ),
    path(
        "<uuid:pk>/lines/<uuid:line_pk>/receive-serialized/",
        views.PurchaseRequestLineSerializedReceiveView.as_view(),
        name="line-receive-serialized",
    ),
]
