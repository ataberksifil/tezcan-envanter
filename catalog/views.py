from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from catalog.forms import CategoryForm
from catalog.models import Category

CATEGORY_LIST_PAGE_SIZE = 50
STATUS_ALL = "all"
STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"
ALLOWED_STATUS_FILTERS = frozenset({STATUS_ALL, STATUS_ACTIVE, STATUS_INACTIVE})


def normalize_category_status(value: str | None) -> str:
    if value in ALLOWED_STATUS_FILTERS:
        return value
    return STATUS_ALL


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
        messages.success(self.request, "Kategori oluşturuldu.")
        return super().form_valid(form)


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
        messages.success(self.request, "Kategori güncellendi.")
        return super().form_valid(form)


class CategoryStateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "catalog.change_category"
    http_method_names = ["post"]
    target_active: bool
    success_message: str

    def post(self, request, pk):
        category = get_object_or_404(Category, pk=pk)
        if category.active != self.target_active:
            category.active = self.target_active
            category.save(update_fields=["active", "updated_at"])
        messages.success(request, self.success_message)
        return redirect("catalog:category-list")


class CategoryDeactivateView(CategoryStateView):
    target_active = False
    success_message = "Kategori pasifleştirildi."


class CategoryReactivateView(CategoryStateView):
    target_active = True
    success_message = "Kategori aktifleştirildi."
