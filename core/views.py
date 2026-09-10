import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

logger = logging.getLogger(__name__)


class HomeView(LoginRequiredMixin, TemplateView):
    template_name = 'core/home.html'


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
