from decimal import Decimal, InvalidOperation

from django import template
from django.templatetags.static import static
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from core.ui_session import ROLE_LABEL_SESSION_KEY, build_role_label

register = template.Library()


@register.simple_tag
def icon(name, css_class=""):
    """Render a decorative icon from the local Bootstrap Icons sprite."""
    classes = f"icon {css_class}".strip()
    return format_html(
        '<svg class="{}" aria-hidden="true" focusable="false"><use href="{}#{}"></use></svg>',
        classes,
        static("vendor/bootstrap-icons/icons.svg"),
        name,
    )


@register.simple_tag(takes_context=True)
def nav_state(context, *prefixes):
    """Return active-link attributes when the current path starts with a prefix."""
    request = context.get("request")
    path = getattr(request, "path", "") or ""
    for prefix in prefixes:
        if prefix == "/" and path == "/":
            return mark_safe('aria-current="page" data-active="true"')
        if prefix != "/" and path.startswith(prefix):
            return mark_safe('aria-current="page" data-active="true"')
    return ""


@register.simple_tag(takes_context=True)
def role_label(context, user):
    """Display-only role caption; cached in the session at login (core.ui_session)."""
    request = context.get("request")
    session = getattr(request, "session", None)
    if session is not None:
        label = session.get(ROLE_LABEL_SESSION_KEY)
        if label is None:
            label = build_role_label(user)
            session[ROLE_LABEL_SESSION_KEY] = label
        return label
    return build_role_label(user)


@register.filter
def qty(value):
    """Show a stored quantity in Turkish notation without padded zeros.

    Presentation only: 80.000 -> "80", 2.500 -> "2,5", 1250.750 -> "1.250,75".
    Stored Decimal values and form parsing are unchanged.
    """
    if value is None or value == "":
        return "—"
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    if not number.is_finite():
        return value
    if number == number.to_integral_value():
        number = number.quantize(Decimal(1))
    else:
        number = number.normalize()
    sign = "-" if number < 0 else ""
    integer_part, _, fraction = format(abs(number), "f").partition(".")
    groups = []
    while len(integer_part) > 3:
        groups.insert(0, integer_part[-3:])
        integer_part = integer_part[:-3]
    groups.insert(0, integer_part)
    text = sign + ".".join(groups)
    return f"{text},{fraction}" if fraction else text


# The resolver accepts any of these view permissions (identification.views).
_SCAN_PERMISSIONS = ("catalog.view_material", "inventory.view_stockbalance", "locations.view_location")


@register.simple_tag
def search_config(user):
    """Describe the single shared "scan or search" field for this user, or None.

    Text search goes to the material catalog when allowed, otherwise to current
    stock. TZ1 labels go to the read-only resolver when the user may scan.
    """
    can_scan = any(user.has_perm(perm) for perm in _SCAN_PERMISSIONS)
    if user.has_perm("catalog.view_material"):
        action, placeholder, short = (
            reverse("catalog:material-list"), "Barkodu okut veya malzeme ara", "Okut veya ara"
        )
    elif user.has_perm("inventory.view_stockbalance"):
        action, placeholder, short = (
            reverse("inventory:stock-list"), "Barkodu okut veya stokta ara", "Okut veya stokta ara"
        )
    elif can_scan:
        action, placeholder, short = None, "Barkodu okut", "Barkodu okut"
    else:
        return None
    return {
        "action": action,
        "placeholder": placeholder,
        "short_placeholder": short,
        "can_scan": can_scan,
        "resolve_url": reverse("identification:resolve") if can_scan else "",
        "scanner_url": reverse("identification:scanner") if can_scan else "",
    }
