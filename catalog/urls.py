from django.urls import path

from catalog import views

app_name = "catalog"

urlpatterns = [
    path("categories/", views.CategoryListView.as_view(), name="category-list"),
    path("categories/new/", views.CategoryCreateView.as_view(), name="category-create"),
    path(
        "categories/<uuid:pk>/edit/",
        views.CategoryUpdateView.as_view(),
        name="category-update",
    ),
    path(
        "categories/<uuid:pk>/deactivate/",
        views.CategoryDeactivateView.as_view(),
        name="category-deactivate",
    ),
    path(
        "categories/<uuid:pk>/reactivate/",
        views.CategoryReactivateView.as_view(),
        name="category-reactivate",
    ),
    path("units/", views.UnitOfMeasureListView.as_view(), name="unit-list"),
    path("units/new/", views.UnitOfMeasureCreateView.as_view(), name="unit-create"),
    path(
        "units/<uuid:pk>/edit/",
        views.UnitOfMeasureUpdateView.as_view(),
        name="unit-update",
    ),
    path(
        "units/<uuid:pk>/deactivate/",
        views.UnitOfMeasureDeactivateView.as_view(),
        name="unit-deactivate",
    ),
    path(
        "units/<uuid:pk>/reactivate/",
        views.UnitOfMeasureReactivateView.as_view(),
        name="unit-reactivate",
    ),
    path("materials/", views.MaterialListView.as_view(), name="material-list"),
    path("materials/new/", views.MaterialCreateView.as_view(), name="material-create"),
    path(
        "materials/<uuid:pk>/",
        views.MaterialDetailView.as_view(),
        name="material-detail",
    ),
    path(
        "materials/<uuid:pk>/edit/",
        views.MaterialUpdateView.as_view(),
        name="material-update",
    ),
    path(
        "materials/<uuid:pk>/deactivate/",
        views.MaterialDeactivateView.as_view(),
        name="material-deactivate",
    ),
    path(
        "materials/<uuid:pk>/reactivate/",
        views.MaterialReactivateView.as_view(),
        name="material-reactivate",
    ),
]
