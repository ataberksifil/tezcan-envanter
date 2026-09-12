import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


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


class Employee(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee_number = models.CharField(max_length=64)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="employee",
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["employee_number"],
                name="accounts_employee_number_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(employee_number__regex=r"^\s*$"),
                name="accounts_employee_number_nonblank",
            ),
            models.CheckConstraint(
                condition=~Q(first_name__regex=r"^\s*$"),
                name="accounts_employee_first_name_nonblank",
            ),
            models.CheckConstraint(
                condition=~Q(last_name__regex=r"^\s*$"),
                name="accounts_employee_last_name_nonblank",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.last_name}, {self.first_name} ({self.employee_number})"

    def clean(self) -> None:
        super().clean()

        if self.employee_number is not None:
            self.employee_number = self.employee_number.strip()
            if not self.employee_number:
                raise ValidationError(
                    {"employee_number": "Sicil numarası boş olamaz."}
                )

        if self.first_name is not None:
            self.first_name = self.first_name.strip()
            if not self.first_name:
                raise ValidationError({"first_name": "Ad boş olamaz."})

        if self.last_name is not None:
            self.last_name = self.last_name.strip()
            if not self.last_name:
                raise ValidationError({"last_name": "Soyad boş olamaz."})
