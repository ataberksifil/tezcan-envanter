from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.urls import include, path
from django.views import View

from accounts.roles import user_has_catalog_view_permission


class CatalogPermissionView(View):
    """Test-only stand-in until catalog routes exist.

    Authorization uses the same catalog view permission helper that
    navigation consults. Hidden links are not the control.
    """

    def get(self, request, *args, **kwargs):
        if not user_has_catalog_view_permission(request.user):
            raise PermissionDenied
        return HttpResponse("catalog-ok")


catalog_urlpatterns = [
    path("", CatalogPermissionView.as_view(), name="index"),
]

urlpatterns = [
    path("", include("config.urls")),
    path("catalog/", include((catalog_urlpatterns, "catalog"))),
]
