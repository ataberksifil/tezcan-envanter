from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """
    Project-owned auth user (DEC-019).

    Keep minimal until business decisions (e.g. DEC-HG-004) are resolved.
    Employee remains a separate domain concept in a later task.
    """

    class Meta(AbstractUser.Meta):
        permissions = [
            ("manage_access", "Rol ve kullanıcı erişimlerini yönetebilir"),
        ]
