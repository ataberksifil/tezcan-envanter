from django.contrib import admin
from django.contrib.auth.models import Group

# Role and user-role writes must use the audited application service.
try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass
