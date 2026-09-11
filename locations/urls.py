from django.urls import path

from locations import views

app_name = "locations"

urlpatterns = [
    path("", views.LocationListView.as_view(), name="location-list"),
    path("new/", views.LocationCreateView.as_view(), name="location-create"),
    path("<uuid:pk>/", views.LocationDetailView.as_view(), name="location-detail"),
    path(
        "<uuid:pk>/edit/",
        views.LocationUpdateView.as_view(),
        name="location-update",
    ),
    path(
        "<uuid:pk>/deactivate/",
        views.LocationDeactivateView.as_view(),
        name="location-deactivate",
    ),
    path(
        "<uuid:pk>/reactivate/",
        views.LocationReactivateView.as_view(),
        name="location-reactivate",
    ),
]
