from __future__ import annotations

from django import forms

from accounts.roles import SAFE_CATALOG_PERMISSION_LABELS


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
)
assert tuple(value for value, _label in PERMISSION_CHOICES) == SAFE_CATALOG_PERMISSION_LABELS


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
        label="Katalog izinleri",
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
