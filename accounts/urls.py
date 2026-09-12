from django.urls import path

from accounts import employee_views, views

app_name = "accounts"

urlpatterns = [
    path("employees/", employee_views.EmployeeListView.as_view(), name="employee-list"),
    path(
        "employees/new/",
        employee_views.EmployeeCreateView.as_view(),
        name="employee-create",
    ),
    path(
        "employees/<uuid:pk>/",
        employee_views.EmployeeDetailView.as_view(),
        name="employee-detail",
    ),
    path(
        "employees/<uuid:pk>/edit/",
        employee_views.EmployeeUpdateView.as_view(),
        name="employee-update",
    ),
    path(
        "employees/<uuid:pk>/deactivate/",
        employee_views.EmployeeDeactivateView.as_view(),
        name="employee-deactivate",
    ),
    path(
        "employees/<uuid:pk>/reactivate/",
        employee_views.EmployeeReactivateView.as_view(),
        name="employee-reactivate",
    ),
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
