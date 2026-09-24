"""Display-only role label kept in the session so the shell adds no query per page.

Authorization never reads this value; it only feeds the top-bar caption. A role
change shows after the next login.
"""

ROLE_LABEL_SESSION_KEY = 'ui_role_label'

# Display names for the default role templates. Renamed or custom Groups keep their own names.
_ROLE_DISPLAY = {
    'ADMIN_MANAGER': 'Yönetici',
    'STOREKEEPER': 'Ambar sorumlusu',
    'TECHNICIAN': 'Teknisyen',
}


def build_role_label(user) -> str:
    names = [_ROLE_DISPLAY.get(name, name) for name in user.groups.values_list('name', flat=True)]
    return ', '.join(names) if names else 'Rol atanmamış'


def remember_role_label(sender, request, user, **kwargs):
    if request is not None and hasattr(request, 'session'):
        request.session[ROLE_LABEL_SESSION_KEY] = build_role_label(user)
