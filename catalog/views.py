from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from catalog.forms import CategoryForm
from catalog.models import Category
from catalog.services.categories import (
    create_category,
    set_category_active,
    update_category,
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
