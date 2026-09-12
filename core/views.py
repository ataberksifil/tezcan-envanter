import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from core.management_access import (
    user_can_manage_categories,
    user_can_manage_employees,
    user_can_manage_locations,
    user_can_manage_materials,
    user_can_manage_production_lines,
    user_can_manage_units,
    user_has_management_access,
)

logger = logging.getLogger(__name__)


class HomeView(LoginRequiredMixin, TemplateView):
    template_name = 'core/home.html'


class ManagementView(LoginRequiredMixin, TemplateView):
    template_name = 'core/management.html'
    http_method_names = ['get', 'head']

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not user_has_management_access(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context['show_category_management'] = user_can_manage_categories(user)
        context['show_unit_management'] = user_can_manage_units(user)
        context['show_material_management'] = user_can_manage_materials(user)
        context['show_location_management'] = user_can_manage_locations(user)
        context['show_employee_management'] = user_can_manage_employees(user)
        context['show_production_line_management'] = user_can_manage_production_lines(
            user
        )
        context['show_access_management'] = user.has_perm('accounts.manage_access')
        return context


@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            result = cursor.fetchone()
        if result != (1,):
            return JsonResponse(
                {'status': 'unavailable', 'database': 'unavailable'},
                status=503,
            )
    except DatabaseError:
        logger.warning('Health check database probe failed.')
        return JsonResponse(
            {'status': 'unavailable', 'database': 'unavailable'},
            status=503,
        )
    return JsonResponse({'status': 'ok', 'database': 'ok'})
