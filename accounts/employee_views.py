from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import DetailView, FormView, ListView

from accounts.forms import EmployeeForm
from accounts.models import Employee
from accounts.services.employees import (
    create_employee,
    set_employee_active,
    update_employee,
)

EMPLOYEE_LIST_PAGE_SIZE = 50
STATUS_ALL = "all"
STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"
ALLOWED_STATUS_FILTERS = frozenset({STATUS_ALL, STATUS_ACTIVE, STATUS_INACTIVE})


def normalize_status_filter(value: str | None) -> str:
    if value in ALLOWED_STATUS_FILTERS:
        return value
    return STATUS_ALL


def _attach_validation_error(form, exc: ValidationError) -> None:
    if hasattr(exc, "error_dict"):
        for field, errors in exc.error_dict.items():
            if field == "__all__":
                for error in errors:
                    form.add_error(None, error)
            else:
                for error in errors:
                    form.add_error(field, error)
    else:
        form.add_error(None, exc)


class EmployeeListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "accounts.view_employee"
    model = Employee
    context_object_name = "employees"
    template_name = "accounts/employee_list.html"
    paginate_by = EMPLOYEE_LIST_PAGE_SIZE

    def get_queryset(self):
        queryset = Employee.objects.order_by(
            "last_name", "first_name", "employee_number", "id"
        )
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(employee_number__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
            )

        status = self._status_filter()
        if status == STATUS_ACTIVE:
            queryset = queryset.filter(active=True)
        elif status == STATUS_INACTIVE:
            queryset = queryset.filter(active=False)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["q"] = self.request.GET.get("q", "").strip()
        context["status"] = self._status_filter()
        return context

    def _status_filter(self) -> str:
        return normalize_status_filter(self.request.GET.get("status", STATUS_ALL))


class EmployeeDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = "accounts.view_employee"
    model = Employee
    context_object_name = "employee"
    template_name = "accounts/employee_detail.html"

    def get_queryset(self):
        return Employee.objects.select_related("user")


class EmployeeCreateView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    permission_required = "accounts.add_employee"
    form_class = EmployeeForm
    template_name = "accounts/employee_form.html"
    success_url = reverse_lazy("accounts:employee-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["employee"] = None
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Yeni çalışan"
        context["submit_label"] = "Kaydet"
        return context

    def form_valid(self, form):
        linked_user = form.cleaned_data.get("user")
        try:
            result = create_employee(
                actor=self.request.user,
                employee_number=form.cleaned_data["employee_number"],
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
                user_id=linked_user.pk if linked_user is not None else None,
            )
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self.form_invalid(form)
        self.object = result.employee
        messages.success(self.request, "Çalışan oluşturuldu.")
        return redirect(self.get_success_url())


class EmployeeUpdateView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    permission_required = "accounts.change_employee"
    form_class = EmployeeForm
    template_name = "accounts/employee_form.html"
    success_url = reverse_lazy("accounts:employee-list")
    context_object_name = "employee"

    def dispatch(self, request, *args, **kwargs):
        try:
            self.object = Employee.objects.get(pk=kwargs["pk"])
        except Employee.DoesNotExist as exc:
            raise Http404("Çalışan bulunamadı.") from exc
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["employee"] = self.object
        return kwargs

    def get_initial(self):
        return {
            "employee_number": self.object.employee_number,
            "first_name": self.object.first_name,
            "last_name": self.object.last_name,
            "user": self.object.user_id,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["employee"] = self.object
        context["form_title"] = "Çalışanı düzenle"
        context["submit_label"] = "Kaydet"
        return context

    def form_valid(self, form):
        linked_user = form.cleaned_data.get("user")
        try:
            result = update_employee(
                actor=self.request.user,
                employee_id=self.object.pk,
                employee_number=form.cleaned_data["employee_number"],
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
                user_id=linked_user.pk if linked_user is not None else None,
            )
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self.form_invalid(form)
        self.object = result.employee
        if result.changed:
            messages.success(self.request, "Çalışan güncellendi.")
        else:
            messages.info(self.request, "Değişiklik yapılmadı.")
        return redirect(self.get_success_url())


class EmployeeStateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "accounts.change_employee"
    http_method_names = ["post"]
    target_active: bool
    success_message: str
    noop_message: str

    def post(self, request, pk):
        try:
            result = set_employee_active(
                actor=request.user,
                employee_id=pk,
                active=self.target_active,
            )
        except Employee.DoesNotExist as exc:
            raise Http404("Çalışan bulunamadı.") from exc
        if result.changed:
            messages.success(request, self.success_message)
        else:
            messages.info(request, self.noop_message)
        return redirect("accounts:employee-list")


class EmployeeDeactivateView(EmployeeStateView):
    target_active = False
    success_message = "Çalışan pasifleştirildi."
    noop_message = "Çalışan zaten pasif."


class EmployeeReactivateView(EmployeeStateView):
    target_active = True
    success_message = "Çalışan aktifleştirildi."
    noop_message = "Çalışan zaten aktif."
