"""Read-only presentation helpers for inventory screens."""

from datetime import timedelta

from django import template
from django.utils import timezone

from inventory.models import SKT_APPROACHING_WINDOW_DAYS, ReceiptExpiry

register = template.Library()


@register.simple_tag
def expiry_counts():
    """Counts matching the SKT warning list: expired and approaching receipts."""
    today = timezone.localdate()
    horizon = today + timedelta(days=SKT_APPROACHING_WINDOW_DAYS)
    return {
        "expired": ReceiptExpiry.objects.filter(expires_on__lt=today).count(),
        "approaching": ReceiptExpiry.objects.filter(
            expires_on__gte=today, expires_on__lte=horizon
        ).count(),
    }
