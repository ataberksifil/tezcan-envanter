from django.urls import path

from identification import views

app_name = "identification"

urlpatterns = [
    path("scan/", views.ScannerView.as_view(), name="scanner"),
    path("resolve/", views.ResolveView.as_view(), name="resolve"),
    path(
        "materials/<uuid:pk>/qr.png",
        views.MaterialQRImageView.as_view(),
        name="material-image",
    ),
    path(
        "materials/<uuid:pk>/code128.svg",
        views.MaterialCode128ImageView.as_view(),
        name="material-code128",
    ),
    path(
        "materials/<uuid:pk>/label/",
        views.MaterialLabelView.as_view(),
        name="material-label",
        kwargs={"profile": "standard"},
    ),
    path(
        "materials/<uuid:pk>/label/compact/",
        views.MaterialLabelView.as_view(),
        name="material-label-compact",
        kwargs={"profile": "compact"},
    ),
    path(
        "assets/<uuid:pk>/qr.png",
        views.SerializedAssetQRImageView.as_view(),
        name="asset-image",
    ),
    path(
        "assets/<uuid:pk>/code128.svg",
        views.SerializedAssetCode128ImageView.as_view(),
        name="asset-code128",
    ),
    path(
        "assets/<uuid:pk>/label/",
        views.SerializedAssetLabelView.as_view(),
        name="asset-label",
        kwargs={"profile": "standard"},
    ),
    path(
        "assets/<uuid:pk>/label/compact/",
        views.SerializedAssetLabelView.as_view(),
        name="asset-label-compact",
        kwargs={"profile": "compact"},
    ),
    path(
        "locations/<uuid:pk>/qr.png",
        views.LocationQRImageView.as_view(),
        name="location-image",
    ),
    path(
        "locations/<uuid:pk>/code128.svg",
        views.LocationCode128ImageView.as_view(),
        name="location-code128",
    ),
    path(
        "locations/<uuid:pk>/label/",
        views.LocationLabelView.as_view(),
        name="location-label",
        kwargs={"profile": "standard"},
    ),
    path(
        "locations/<uuid:pk>/label/compact/",
        views.LocationLabelView.as_view(),
        name="location-label-compact",
        kwargs={"profile": "compact"},
    ),
]
