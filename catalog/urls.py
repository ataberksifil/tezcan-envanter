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
]
