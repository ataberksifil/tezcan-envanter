from django import template

from core.management_access import user_has_management_access

register = template.Library()


@register.simple_tag
def show_management_nav(user) -> bool:
    return user_has_management_access(user)
