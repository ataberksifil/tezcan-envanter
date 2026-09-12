from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model

from accounts.models import Employee
from accounts.roles import SAFE_CATALOG_PERMISSION_LABELS

User = get_user_model()


PERMISSION_CHOICES = (
    ("catalog.view_category", "Kategori — görüntüleme"),
    ("catalog.add_category", "Kategori — oluşturma"),
    ("catalog.change_category", "Kategori — değiştirme"),
    ("catalog.view_unitofmeasure", "Ölçü birimi — görüntüleme"),
    ("catalog.add_unitofmeasure", "Ölçü birimi — oluşturma"),
    ("catalog.change_unitofmeasure", "Ölçü birimi — değiştirme"),
    ("catalog.view_material", "Malzeme — görüntüleme"),
    ("catalog.add_material", "Malzeme — oluşturma"),
    ("catalog.change_material", "Malzeme — değiştirme"),
    ("locations.view_location", "Lokasyon — görüntüleme"),
    ("locations.add_location", "Lokasyon — oluşturma"),
    ("locations.change_location", "Lokasyon — değiştirme"),
    ("accounts.view_employee", "Çalışan — görüntüleme"),
    ("accounts.add_employee", "Çalışan — oluşturma"),
    ("accounts.change_employee", "Çalışan — değiştirme"),
    ("inventory.view_productionline", "Üretim hattı — görüntüleme"),
    ("inventory.add_productionline", "Üretim hattı — oluşturma"),
    ("inventory.change_productionline", "Üretim hattı — değiştirme"),
    ("inventory.receive_stock", "Stok girişi yapabilir"),
    ("inventory.issue_stock", "Stok çıkışı yapabilir"),
)
assert tuple(value for value, _label in PERMISSION_CHOICES) == SAFE_CATALOG_PERMISSION_LABELS


def user_choice_label(user: User) -> str:
    parts = [user.get_username()]
    if not user.is_active:
        parts.append("[Pasif]")
    parts.append(f"— {user.pk}")
    return " ".join(parts)


def eligible_users_for_employee(employee: Employee | None = None):
    linked_elsewhere = Employee.objects.filter(user__isnull=False)
    if employee is not None and employee.pk is not None:
        linked_elsewhere = linked_elsewhere.exclude(pk=employee.pk)
    excluded_ids = linked_elsewhere.values_list("user_id", flat=True)
    return User.objects.exclude(pk__in=excluded_ids).order_by("username", "pk")


class EmployeeForm(forms.Form):
    employee_number = forms.CharField(
        label="Sicil numarası",
        max_length=64,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    first_name = forms.CharField(
        label="Ad",
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    last_name = forms.CharField(
        label="Soyad",
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    user = forms.ModelChoiceField(
        label="Bağlı kullanıcı",
        queryset=User.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, employee: Employee | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.employee = employee
        eligible = eligible_users_for_employee(employee)
        self.fields["user"].queryset = eligible
        self.fields["user"].empty_label = "Kullanıcı bağlı değil"
        self.fields["user"].label_from_instance = user_choice_label
        self.fields["user"].widget.choices = [
            ("", self.fields["user"].empty_label),
            *[(str(user.pk), user_choice_label(user)) for user in eligible],
        ]


class RoleCreateForm(forms.Form):
    name = forms.CharField(
        label="Rol adı",
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )


class RoleRenameForm(RoleCreateForm):
    pass


class RolePermissionsForm(forms.Form):
    catalog_permissions = forms.MultipleChoiceField(
        label="Katalog, lokasyon ve çalışan izinleri",
        choices=PERMISSION_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    manage_access = forms.BooleanField(
        label="Rol ve kullanıcı erişimlerini yönetebilir",
        required=False,
    )

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        if not actor.is_superuser:
            self.fields.pop("manage_access")


class UserRolesForm(forms.Form):
    roles = forms.MultipleChoiceField(
        label="Roller",
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, roles, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["roles"].choices = [(str(role.pk), role.name) for role in roles]
