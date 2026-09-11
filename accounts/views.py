from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect
from django.views.generic import FormView, ListView, TemplateView

from accounts.forms import (
    RoleCreateForm,
    RolePermissionsForm,
    RoleRenameForm,
    UserRolesForm,
)
from accounts.roles import MANAGE_ACCESS_PERMISSION, SAFE_CATALOG_PERMISSION_SET
from accounts.services.access_management import (
    assignable_roles_for,
    canonical_user_snapshot,
    create_role,
    is_supported_role,
    rename_role,
    role_permission_labels,
    role_permissions_protection_reason,
    role_rename_protection_reason,
    set_role_permissions,
    set_user_roles,
    user_mutation_protection_reason,
)

User = get_user_model()


def _attach_validation_error(form, exc: ValidationError) -> None:
    if hasattr(exc, "error_dict"):
        for field, errors in exc.error_dict.items():
            target = None if field == "__all__" or field not in form.fields else field
            for error in errors:
                form.add_error(target, error)
    else:
        form.add_error(None, exc)


class AccessManagementMixin(LoginRequiredMixin, PermissionRequiredMixin):
    permission_required = MANAGE_ACCESS_PERMISSION


class RoleListView(AccessManagementMixin, TemplateView):
    template_name = "accounts/role_list.html"
    http_method_names = ["get", "head"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        rows = []
        for role in Group.objects.order_by("name", "pk"):
            labels = role_permission_labels(role)
            rows.append(
                {
                    "role": role,
                    "member_count": role.user_set.count(),
                    "safe_permissions": sorted(labels & SAFE_CATALOG_PERMISSION_SET),
                    "manages_access": MANAGE_ACCESS_PERMISSION in labels,
                    "supported": is_supported_role(role),
                    "rename_protection": role_rename_protection_reason(
                        self.request.user, role
                    ),
                    "permission_protection": role_permissions_protection_reason(
                        self.request.user, role
                    ),
                }
            )
        context["role_rows"] = rows
        return context


class RoleCreateView(AccessManagementMixin, FormView):
    form_class = RoleCreateForm
    template_name = "accounts/role_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(form_title="Yeni rol", submit_label="Rolü oluştur")
        return context

    def form_valid(self, form):
        try:
            create_role(actor=self.request.user, name=form.cleaned_data["name"])
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self.form_invalid(form)
        messages.success(self.request, "Rol oluşturuldu.")
        return redirect("accounts:role-list")


class RoleObjectMixin:
    role: Group

    def dispatch(self, request, *args, **kwargs):
        try:
            self.role = Group.objects.get(pk=kwargs["pk"])
        except Group.DoesNotExist as exc:
            raise Http404("Rol bulunamadı.") from exc
        return super().dispatch(request, *args, **kwargs)


class RoleRenameView(RoleObjectMixin, AccessManagementMixin, FormView):
    form_class = RoleRenameForm
    template_name = "accounts/role_form.html"

    def get(self, request, *args, **kwargs):
        if role_rename_protection_reason(request.user, self.role):
            raise PermissionDenied
        return super().get(request, *args, **kwargs)

    def get_initial(self):
        return {"name": self.role.name}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(form_title="Rolü yeniden adlandır", submit_label="Kaydet")
        return context

    def form_valid(self, form):
        try:
            result = rename_role(
                actor=self.request.user,
                role_id=self.role.pk,
                name=form.cleaned_data["name"],
            )
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self.form_invalid(form)
        if result.changed:
            messages.success(self.request, "Rol adı güncellendi.")
        else:
            messages.info(self.request, "Değişiklik yapılmadı.")
        return redirect("accounts:role-list")


class RolePermissionsView(RoleObjectMixin, AccessManagementMixin, FormView):
    form_class = RolePermissionsForm
    template_name = "accounts/role_permissions.html"

    def get(self, request, *args, **kwargs):
        if role_permissions_protection_reason(request.user, self.role):
            raise PermissionDenied
        return super().get(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["actor"] = self.request.user
        return kwargs

    def get_initial(self):
        labels = role_permission_labels(self.role)
        return {
            "catalog_permissions": sorted(labels & SAFE_CATALOG_PERMISSION_SET),
            "manage_access": MANAGE_ACCESS_PERMISSION in labels,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["role"] = self.role
        return context

    def form_valid(self, form):
        try:
            result = set_role_permissions(
                actor=self.request.user,
                role_id=self.role.pk,
                catalog_permissions=form.cleaned_data["catalog_permissions"],
                manage_access=form.cleaned_data.get("manage_access"),
            )
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self.form_invalid(form)
        if result.changed:
            messages.success(self.request, "Rol izinleri güncellendi.")
        else:
            messages.info(self.request, "Değişiklik yapılmadı.")
        return redirect("accounts:role-list")


class AccessUserListView(AccessManagementMixin, ListView):
    model = User
    context_object_name = "access_users"
    template_name = "accounts/user_list.html"
    paginate_by = 50

    def get_queryset(self):
        return User.objects.prefetch_related("groups", "user_permissions").order_by(
            "username", "pk"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["user_rows"] = [
            {
                "user": user,
                "roles": list(user.groups.order_by("pk")),
                "protection": user_mutation_protection_reason(self.request.user, user),
            }
            for user in context["access_users"]
        ]
        return context


class AccessUserRolesView(AccessManagementMixin, FormView):
    form_class = UserRolesForm
    template_name = "accounts/user_roles.html"
    target: User

    def dispatch(self, request, *args, **kwargs):
        try:
            self.target = User.objects.get(pk=kwargs["pk"])
        except User.DoesNotExist as exc:
            raise Http404("Kullanıcı bulunamadı.") from exc
        self.selectable_roles = []
        if request.user.is_authenticated:
            if user_mutation_protection_reason(request.user, self.target):
                raise PermissionDenied
            self.selectable_roles = assignable_roles_for(request.user, self.target)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["roles"] = self.selectable_roles
        return kwargs

    def get_initial(self):
        return {
            "roles": [
                str(pk) for pk in self.target.groups.values_list("pk", flat=True)
            ]
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["target_user"] = self.target
        context["snapshot"] = canonical_user_snapshot(self.target)
        return context

    def form_valid(self, form):
        try:
            result = set_user_roles(
                actor=self.request.user,
                user_id=self.target.pk,
                role_ids=form.cleaned_data["roles"],
            )
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self.form_invalid(form)
        if result.changed:
            messages.success(self.request, "Kullanıcı rolleri güncellendi.")
        else:
            messages.info(self.request, "Değişiklik yapılmadı.")
        return redirect("accounts:access-user-list")
