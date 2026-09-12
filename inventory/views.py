from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from inventory.forms import (
    ProductionLineForm,
    QuantityIssueForm,
    QuantityReceiptForm,
    attach_issue_validation_error,
    attach_receipt_validation_error,
)
from inventory.models import InventoryTransaction, ProductionLine
from inventory.services.issues import issue_quantity
from inventory.services.production_lines import (
    create_production_line,
    set_production_line_active,
    update_production_line,
)
from inventory.services.receipts import receive_quantity

PRODUCTION_LINE_LIST_PAGE_SIZE = 50
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


class ProductionLineListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "inventory.view_productionline"
    model = ProductionLine
    context_object_name = "production_lines"
    template_name = "inventory/production_line_list.html"
    paginate_by = PRODUCTION_LINE_LIST_PAGE_SIZE

    def get_queryset(self):
        queryset = ProductionLine.objects.select_related("parent").order_by("name", "id")
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query) | Q(code__icontains=query)
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


class ProductionLineDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = "inventory.view_productionline"
    model = ProductionLine
    context_object_name = "production_line"
    template_name = "inventory/production_line_detail.html"

    def get_queryset(self):
        return ProductionLine.objects.select_related("parent")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["children"] = (
            ProductionLine.objects.filter(parent_id=self.object.pk)
            .order_by("name", "id")
        )
        return context


class ProductionLineCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    permission_required = "inventory.add_productionline"
    form_class = ProductionLineForm
    template_name = "inventory/production_line_form.html"
    success_url = reverse_lazy("inventory:production-line-list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Yeni üretim hattı"
        context["submit_label"] = "Kaydet"
        return context

    def form_valid(self, form):
        parent = form.cleaned_data.get("parent")
        try:
            result = create_production_line(
                actor=self.request.user,
                code=form.cleaned_data["code"],
                name=form.cleaned_data["name"],
                parent_id=parent.pk if parent is not None else None,
            )
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self.form_invalid(form)
        self.object = result.production_line
        messages.success(self.request, "Üretim hattı oluşturuldu.")
        return redirect(self.get_success_url())


class ProductionLineUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    permission_required = "inventory.change_productionline"
    model = ProductionLine
    form_class = ProductionLineForm
    template_name = "inventory/production_line_form.html"
    success_url = reverse_lazy("inventory:production-line-list")
    context_object_name = "production_line"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Üretim hattını düzenle"
        context["submit_label"] = "Kaydet"
        return context

    def form_valid(self, form):
        parent = form.cleaned_data.get("parent")
        try:
            result = update_production_line(
                actor=self.request.user,
                production_line_id=self.object.pk,
                code=form.cleaned_data["code"],
                name=form.cleaned_data["name"],
                parent_id=parent.pk if parent is not None else None,
            )
        except ProductionLine.DoesNotExist as exc:
            raise Http404("No production line found matching the query") from exc
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self.form_invalid(form)
        self.object = result.production_line
        if result.changed:
            messages.success(self.request, "Üretim hattı güncellendi.")
        else:
            messages.success(self.request, "Değişiklik yapılmadı.")
        return redirect(self.get_success_url())


class ProductionLineStateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "inventory.change_productionline"
    http_method_names = ["post"]
    target_active: bool
    success_message: str
    noop_message: str

    def post(self, request, pk):
        try:
            result = set_production_line_active(
                actor=request.user,
                production_line_id=pk,
                active=self.target_active,
            )
        except ProductionLine.DoesNotExist as exc:
            raise Http404("No production line found matching the query") from exc
        if result.changed:
            messages.success(request, self.success_message)
        else:
            messages.success(request, self.noop_message)
        return redirect("inventory:production-line-list")


class ProductionLineDeactivateView(ProductionLineStateView):
    target_active = False
    success_message = "Üretim hattı pasifleştirildi."
    noop_message = "Üretim hattı zaten pasif."


class ProductionLineReactivateView(ProductionLineStateView):
    target_active = True
    success_message = "Üretim hattı aktifleştirildi."
    noop_message = "Üretim hattı zaten aktif."


class ReceiptCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "inventory.receive_stock"
    template_name = "inventory/receipt_form.html"

    def get(self, request):
        form = QuantityReceiptForm()
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "form_title": "Stok girişi",
                "submit_label": "Kaydet",
            },
        )

    def post(self, request):
        form = QuantityReceiptForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "form_title": "Stok girişi",
                    "submit_label": "Kaydet",
                },
            )

        material = form.cleaned_data["material"]
        try:
            result = receive_quantity(
                actor=request.user,
                operation_id=form.cleaned_data["operation_id"],
                material_id=material.pk,
                unit_id=material.unit_id,
                condition_id=form.cleaned_data["condition"].pk,
                target_location_id=form.cleaned_data["target_location"].pk,
                quantity=form.cleaned_data["quantity"],
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            attach_receipt_validation_error(form, exc)
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "form_title": "Stok girişi",
                    "submit_label": "Kaydet",
                },
            )

        if result.replayed:
            messages.info(request, "Bu stok girişi daha önce kaydedilmişti.")
        else:
            messages.success(request, "Stok girişi kaydedildi.")
        return redirect("inventory:receipt-detail", pk=result.transaction.pk)


class IssueCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "inventory.issue_stock"
    template_name = "inventory/issue_form.html"

    def get(self, request):
        form = QuantityIssueForm()
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "form_title": "Stok çıkışı",
                "submit_label": "Kaydet",
            },
        )

    def post(self, request):
        form = QuantityIssueForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "form_title": "Stok çıkışı",
                    "submit_label": "Kaydet",
                },
            )

        material = form.cleaned_data["material"]
        try:
            result = issue_quantity(
                actor=request.user,
                operation_id=form.cleaned_data["operation_id"],
                material_id=material.pk,
                unit_id=material.unit_id,
                condition_id=form.cleaned_data["condition"].pk,
                source_location_id=form.cleaned_data["source_location"].pk,
                quantity=form.cleaned_data["quantity"],
                receiver_employee_id=form.cleaned_data["receiver_employee"].pk,
                production_line_id=form.cleaned_data["production_line"].pk,
                usage_location_text=form.cleaned_data["usage_location_text"],
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            attach_issue_validation_error(form, exc)
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "form_title": "Stok çıkışı",
                    "submit_label": "Kaydet",
                },
            )

        if result.replayed:
            messages.info(request, "Bu stok çıkışı daha önce kaydedilmişti.")
        else:
            messages.success(request, "Stok çıkışı kaydedildi.")
        return redirect("inventory:issue-detail", pk=result.transaction.pk)


class IssueDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = "inventory.issue_stock"
    model = InventoryTransaction
    context_object_name = "issue"
    template_name = "inventory/issue_detail.html"

    def get_queryset(self):
        return (
            InventoryTransaction.objects.filter(
                transaction_type=InventoryTransaction.TransactionType.ISSUE,
            )
            .select_related("acting_user", "issue_context")
            .prefetch_related(
                "lines__material__unit",
                "lines__condition",
                "lines__source_location",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lines = list(self.object.lines.all())
        if len(lines) != 1:
            raise Http404("Issue not found")
        context["line"] = lines[0]
        context["issue_context"] = self.object.issue_context
        return context


class ReceiptDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = "inventory.receive_stock"
    model = InventoryTransaction
    context_object_name = "receipt"
    template_name = "inventory/receipt_detail.html"

    def get_queryset(self):
        return (
            InventoryTransaction.objects.filter(
                transaction_type=InventoryTransaction.TransactionType.RECEIPT,
            )
            .select_related("acting_user")
            .prefetch_related(
                "lines__material__unit",
                "lines__condition",
                "lines__target_location",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lines = list(self.object.lines.all())
        if len(lines) != 1:
            raise Http404("Receipt not found")
        context["line"] = lines[0]
        return context
