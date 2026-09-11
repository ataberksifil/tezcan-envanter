import json
import uuid

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from catalog.forms import CategoryForm, UnitOfMeasureForm
from catalog.models import Category, Material, UnitOfMeasure
from catalog.services.categories import (
    create_category,
    set_category_active,
    update_category,
)
from catalog.services.units_of_measure import (
    create_unit_of_measure,
    set_unit_of_measure_active,
    update_unit_of_measure,
)

CATEGORY_LIST_PAGE_SIZE = 50
STATUS_ALL = "all"
STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"
ALLOWED_STATUS_FILTERS = frozenset({STATUS_ALL, STATUS_ACTIVE, STATUS_INACTIVE})


def normalize_category_status(value: str | None) -> str:
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


class CategoryListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "catalog.view_category"
    model = Category
    context_object_name = "categories"
    template_name = "catalog/category_list.html"
    paginate_by = CATEGORY_LIST_PAGE_SIZE

    def get_queryset(self):
        queryset = Category.objects.select_related("parent").order_by("name", "id")
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(Q(name__icontains=query) | Q(code__icontains=query))

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
        return normalize_category_status(self.request.GET.get("status", STATUS_ALL))


class CategoryCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    permission_required = "catalog.add_category"
    form_class = CategoryForm
    template_name = "catalog/category_form.html"
    success_url = reverse_lazy("catalog:category-list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Yeni kategori"
        context["submit_label"] = "Kaydet"
        return context

    def form_valid(self, form):
        parent = form.cleaned_data.get("parent")
        try:
            result = create_category(
                actor=self.request.user,
                code=form.cleaned_data.get("code"),
                name=form.cleaned_data["name"],
                parent_id=parent.pk if parent is not None else None,
            )
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self.form_invalid(form)
        self.object = result.category
        messages.success(self.request, "Kategori oluşturuldu.")
        return redirect(self.get_success_url())


class CategoryUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    permission_required = "catalog.change_category"
    model = Category
    form_class = CategoryForm
    template_name = "catalog/category_form.html"
    success_url = reverse_lazy("catalog:category-list")
    context_object_name = "category"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Kategoriyi düzenle"
        context["submit_label"] = "Kaydet"
        return context

    def form_valid(self, form):
        parent = form.cleaned_data.get("parent")
        try:
            result = update_category(
                actor=self.request.user,
                category_id=self.object.pk,
                code=form.cleaned_data.get("code"),
                name=form.cleaned_data["name"],
                parent_id=parent.pk if parent is not None else None,
            )
        except Category.DoesNotExist as exc:
            raise Http404("No category found matching the query") from exc
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self.form_invalid(form)
        self.object = result.category
        if result.changed:
            messages.success(self.request, "Kategori güncellendi.")
        else:
            messages.success(self.request, "Değişiklik yapılmadı.")
        return redirect(self.get_success_url())


class CategoryStateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "catalog.change_category"
    http_method_names = ["post"]
    target_active: bool
    success_message: str
    noop_message: str

    def post(self, request, pk):
        try:
            result = set_category_active(
                actor=request.user,
                category_id=pk,
                active=self.target_active,
            )
        except Category.DoesNotExist as exc:
            raise Http404("No category found matching the query") from exc
        if result.changed:
            messages.success(request, self.success_message)
        else:
            messages.success(request, self.noop_message)
        return redirect("catalog:category-list")


class CategoryDeactivateView(CategoryStateView):
    target_active = False
    success_message = "Kategori pasifleştirildi."
    noop_message = "Kategori zaten pasif."


class CategoryReactivateView(CategoryStateView):
    target_active = True
    success_message = "Kategori aktifleştirildi."
    noop_message = "Kategori zaten aktif."


UNIT_LIST_PAGE_SIZE = 50


class UnitOfMeasureListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "catalog.view_unitofmeasure"
    model = UnitOfMeasure
    context_object_name = "units"
    template_name = "catalog/unit_list.html"
    paginate_by = UNIT_LIST_PAGE_SIZE

    def get_queryset(self):
        queryset = UnitOfMeasure.objects.order_by("code", "id")
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(Q(name__icontains=query) | Q(code__icontains=query))

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
        return normalize_category_status(self.request.GET.get("status", STATUS_ALL))


class UnitOfMeasureCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    permission_required = "catalog.add_unitofmeasure"
    form_class = UnitOfMeasureForm
    template_name = "catalog/unit_form.html"
    success_url = reverse_lazy("catalog:unit-list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Yeni ölçü birimi"
        context["submit_label"] = "Kaydet"
        return context

    def form_valid(self, form):
        try:
            result = create_unit_of_measure(
                actor=self.request.user,
                code=form.cleaned_data["code"],
                name=form.cleaned_data["name"],
            )
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self.form_invalid(form)
        self.object = result.unit
        messages.success(self.request, "Ölçü birimi oluşturuldu.")
        return redirect(self.get_success_url())


class UnitOfMeasureUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    permission_required = "catalog.change_unitofmeasure"
    model = UnitOfMeasure
    form_class = UnitOfMeasureForm
    template_name = "catalog/unit_form.html"
    success_url = reverse_lazy("catalog:unit-list")
    context_object_name = "unit"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Ölçü birimini düzenle"
        context["submit_label"] = "Kaydet"
        return context

    def form_valid(self, form):
        try:
            result = update_unit_of_measure(
                actor=self.request.user,
                unit_of_measure_id=self.object.pk,
                code=form.cleaned_data["code"],
                name=form.cleaned_data["name"],
            )
        except UnitOfMeasure.DoesNotExist as exc:
            raise Http404("No unit of measure found matching the query") from exc
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self.form_invalid(form)
        self.object = result.unit
        if result.changed:
            messages.success(self.request, "Ölçü birimi güncellendi.")
        else:
            messages.success(self.request, "Değişiklik yapılmadı.")
        return redirect(self.get_success_url())


class UnitOfMeasureStateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "catalog.change_unitofmeasure"
    http_method_names = ["post"]
    target_active: bool
    success_message: str
    noop_message: str

    def post(self, request, pk):
        try:
            result = set_unit_of_measure_active(
                actor=request.user,
                unit_of_measure_id=pk,
                active=self.target_active,
            )
        except UnitOfMeasure.DoesNotExist as exc:
            raise Http404("No unit of measure found matching the query") from exc
        if result.changed:
            messages.success(request, self.success_message)
        else:
            messages.success(request, self.noop_message)
        return redirect("catalog:unit-list")


class UnitOfMeasureDeactivateView(UnitOfMeasureStateView):
    target_active = False
    success_message = "Ölçü birimi pasifleştirildi."
    noop_message = "Ölçü birimi zaten pasif."


class UnitOfMeasureReactivateView(UnitOfMeasureStateView):
    target_active = True
    success_message = "Ölçü birimi aktifleştirildi."
    noop_message = "Ölçü birimi zaten aktif."


MATERIAL_LIST_PAGE_SIZE = 50
ALLOWED_TRACKING_FILTERS = frozenset(
    {
        Material.TrackingMode.QUANTITY,
        Material.TrackingMode.SERIALIZED,
    }
)


def normalize_tracking_filter(value: str | None) -> str | None:
    if value in ALLOWED_TRACKING_FILTERS:
        return value
    return None


def normalize_category_uuid_filter(value: str | None) -> uuid.UUID | None:
    if not value or not value.strip():
        return None
    try:
        return uuid.UUID(value.strip())
    except ValueError:
        return None


def format_technical_spec_value(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def build_technical_specs_items(specs: dict) -> list[tuple[str, str]]:
    if not specs:
        return []
    return [
        (key, format_technical_spec_value(specs[key]))
        for key in sorted(specs.keys())
    ]


class MaterialListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "catalog.view_material"
    model = Material
    context_object_name = "materials"
    template_name = "catalog/material_list.html"
    paginate_by = MATERIAL_LIST_PAGE_SIZE
    http_method_names = ["get", "head"]

    def get_queryset(self):
        queryset = Material.objects.select_related("category", "unit").order_by(
            "name", "material_code", "id"
        )
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(material_code__icontains=query)
                | Q(name__icontains=query)
                | Q(brand__icontains=query)
                | Q(model__icontains=query)
            )

        category_id = normalize_category_uuid_filter(self.request.GET.get("category"))
        if category_id is not None:
            queryset = queryset.filter(category_id=category_id)

        tracking = normalize_tracking_filter(self.request.GET.get("tracking"))
        if tracking is not None:
            queryset = queryset.filter(tracking_mode=tracking)

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
        context["tracking"] = normalize_tracking_filter(
            self.request.GET.get("tracking")
        )
        category_raw = self.request.GET.get("category", "").strip()
        context["category"] = category_raw
        context["category_filter"] = normalize_category_uuid_filter(category_raw)
        context["filter_categories"] = Category.objects.order_by("name", "id")
        return context

    def _status_filter(self) -> str:
        return normalize_category_status(self.request.GET.get("status", STATUS_ALL))


class MaterialDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = "catalog.view_material"
    model = Material
    context_object_name = "material"
    template_name = "catalog/material_detail.html"
    http_method_names = ["get", "head"]

    def get_queryset(self):
        return Material.objects.select_related("category", "unit")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["technical_specs_items"] = build_technical_specs_items(
            self.object.technical_specs
        )
        return context
