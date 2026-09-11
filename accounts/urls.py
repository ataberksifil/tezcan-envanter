from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("roles/", views.RoleListView.as_view(), name="role-list"),
    path("roles/new/", views.RoleCreateView.as_view(), name="role-create"),
    path("roles/<int:pk>/rename/", views.RoleRenameView.as_view(), name="role-rename"),
    path(
        "roles/<int:pk>/permissions/",
        views.RolePermissionsView.as_view(),
        name="role-permissions",
    ),
    path("users/", views.AccessUserListView.as_view(), name="access-user-list"),
    path(
        "users/<int:pk>/roles/",
        views.AccessUserRolesView.as_view(),
        name="access-user-roles",
    ),
]
