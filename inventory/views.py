from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from catalog.models import Material
from inventory.forms import (
    ProductionLineForm,
    QuantityIssueForm,
    QuantityReceiptForm,
    QuantityReturnForm,
    QuantityTransferForm,
    SerializedIssueForm,
    SerializedReceiptForm,
    SerializedReturnForm,
    SerializedTransferForm,
    attach_issue_validation_error,
    attach_receipt_validation_error,
    attach_return_validation_error,
    attach_serialized_issue_validation_error,
    attach_serialized_receipt_validation_error,
    attach_serialized_return_validation_error,
    attach_serialized_transfer_validation_error,
    attach_transfer_validation_error,
)
from inventory.models import (
    InventoryTransaction,
    InventoryTransactionLine,
    ProductionLine,
    SerializedAsset,
    StockBalance,
)
from inventory.stock_list import (
    STOCK_LIST_PAGE_SIZE,
    STOCK_LIST_PERMISSION,
    build_stock_balance_queryset,
    current_stock_balances_for_material,
    filter_params_from_request as stock_filter_params_from_request,
    stock_list_filter_form_context,
)
from locations.display import location_path_label
from inventory.transaction_history import (
    TRANSACTION_HISTORY_PAGE_SIZE,
    TRANSACTION_HISTORY_PERMISSION,
    build_transaction_history_queryset,
    base_transaction_history_queryset,
    filter_form_context,
    filter_params_from_request,
    normalize_transaction_type_filter,
)
from inventory.services.issues import issue_quantity, issue_serialized
from inventory.services.production_lines import (
    create_production_line,
    set_production_line_active,
    update_production_line,
)
from inventory.services.receipts import receive_quantity, receive_serialized
from inventory.services.returns import return_quantity, return_serialized
from inventory.services.transfers import transfer_quantity, transfer_serialized

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

    def _initial_from_get(self, request):
        initial = {}
        material = request.GET.get("material", "").strip()
        target_location = request.GET.get("target_location", "").strip()
        condition = request.GET.get("condition", "").strip()
        if material:
            initial["material"] = material
        if target_location:
            initial["target_location"] = target_location
        if condition:
            initial["condition"] = condition
        return initial

    def _render(self, request, form, *, preview=None):
        selected_material = None
        if form.is_bound:
            selected_material = form.cleaned_data.get("material") if form.is_valid() else None
            if selected_material is None:
                raw = form.data.get("material")
                if raw:
                    selected_material = Material.objects.filter(
                        pk=raw,
                        tracking_mode=Material.TrackingMode.QUANTITY,
                    ).select_related("unit", "category").first()
        elif form.initial.get("material"):
            selected_material = Material.objects.filter(
                pk=form.initial["material"],
                tracking_mode=Material.TrackingMode.QUANTITY,
            ).select_related("unit", "category").first()
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "form_title": "Stok girişi / mal kabul",
                "submit_label": "Girişi kaydet" if preview else "Önizle",
                "preview": preview,
                "selected_material": selected_material,
                "selected_material_balances": (
                    current_stock_balances_for_material(selected_material.pk)
                    if selected_material is not None
                    and request.user.has_perm(STOCK_LIST_PERMISSION)
                    else None
                ),
            },
        )

    def _preview_context(self, form):
        material = form.cleaned_data["material"]
        location = form.cleaned_data["target_location"]
        condition = form.cleaned_data["condition"]
        quantity = form.cleaned_data["quantity"]
        return {
            "material": material,
            "location": location,
            "location_path": location_path_label(location),
            "condition": condition,
            "quantity": quantity,
            "unit": material.unit,
        }

    def get(self, request):
        material_id = request.GET.get("material", "").strip()
        if material_id:
            material = Material.objects.filter(pk=material_id).first()
            if (
                material is not None
                and material.tracking_mode == Material.TrackingMode.SERIALIZED
            ):
                return redirect(
                    f"{reverse('inventory:serialized-receipt-create')}?material={material.pk}"
                )
        return self._render(request, QuantityReceiptForm(initial=self._initial_from_get(request)))

    def post(self, request):
        form = QuantityReceiptForm(request.POST)
        if not form.is_valid():
            return self._render(request, form)

        if request.POST.get("intent") == "edit":
            return self._render(request, form)

        if request.POST.get("confirm") != "1":
            return self._render(request, form, preview=self._preview_context(form))

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
            return self._render(request, form)

        if result.replayed:
            messages.info(request, "Bu stok girişi daha önce kaydedilmişti.")
        else:
            messages.success(request, "Stok girişi kaydedildi.")
        return redirect("inventory:receipt-detail", pk=result.transaction.pk)


class ReceiptMaterialSummaryView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "inventory.receive_stock"
    http_method_names = ["get", "head"]
    template_name = "inventory/_receipt_material_summary.html"

    def get(self, request):
        material_id = request.GET.get("material", "").strip()
        material = None
        balances = None
        if material_id:
            material = (
                Material.objects.filter(pk=material_id)
                .select_related("unit", "category")
                .first()
            )
            if (
                material is not None
                and material.tracking_mode == Material.TrackingMode.QUANTITY
                and request.user.has_perm(STOCK_LIST_PERMISSION)
            ):
                balances = current_stock_balances_for_material(material.pk)
        return render(
            request,
            self.template_name,
            {
                "selected_material": material,
                "selected_material_balances": balances,
            },
        )


class SerializedReceiptCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "inventory.receive_stock"
    template_name = "inventory/serialized_receipt_form.html"

    def _render(self, request, form):
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "form_title": "Tekil varlık stok girişi",
                "submit_label": "Kaydet",
            },
        )

    def get(self, request):
        initial = {}
        material = request.GET.get("material", "").strip()
        target_location = request.GET.get("target_location", "").strip()
        if material:
            initial["material"] = material
        if target_location:
            initial["target_location"] = target_location
        return self._render(request, SerializedReceiptForm(initial=initial))

    def post(self, request):
        form = SerializedReceiptForm(request.POST)
        if not form.is_valid():
            return self._render(request, form)

        try:
            result = receive_serialized(
                actor=request.user,
                operation_id=form.cleaned_data["operation_id"],
                material_id=form.cleaned_data["material"].pk,
                internal_asset_code=form.cleaned_data["internal_asset_code"],
                serial_number=form.cleaned_data["serial_number"],
                condition_id=form.cleaned_data["condition"].pk,
                target_location_id=form.cleaned_data["target_location"].pk,
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            attach_serialized_receipt_validation_error(form, exc)
            return self._render(request, form)

        if result.replayed:
            messages.info(request, "Bu tekil varlık stok girişi daha önce kaydedilmişti.")
        else:
            messages.success(request, "Tekil varlık stok girişi kaydedildi.")
        return redirect("inventory:receipt-detail", pk=result.transaction.pk)


class SerializedAssetDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = "inventory.view_stockbalance"
    model = SerializedAsset
    context_object_name = "asset"
    template_name = "inventory/serialized_asset_detail.html"
    http_method_names = ["get", "head"]

    def get_queryset(self):
        return SerializedAsset.objects.select_related(
            "material",
            "current_location",
            "current_condition",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.has_perm(TRANSACTION_HISTORY_PERMISSION):
            history = base_transaction_history_queryset().filter(
                lines__serialized_asset_id=self.object.pk
            )
            context["asset_transactions"] = history.order_by(
                "-lines__asset_event_seq", "-id"
            )
        if self.object.current_state == SerializedAsset.CurrentState.ISSUED:
            context["active_issue_line"] = (
                InventoryTransactionLine.objects.filter(
                    serialized_asset_id=self.object.pk,
                    transaction__transaction_type=(
                        InventoryTransaction.TransactionType.ISSUE
                    ),
                    return_lines__isnull=True,
                )
                .select_related("transaction__issue_context", "source_location")
                .order_by("-asset_event_seq", "-id")
                .first()
            )
        return context


class SerializedIssueCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "inventory.issue_stock"
    template_name = "inventory/serialized_issue_form.html"

    def _asset(self, pk):
        return get_object_or_404(
            SerializedAsset.objects.select_related(
                "material", "current_location", "current_condition"
            ),
            pk=pk,
        )

    def _render(self, request, asset, form):
        return render(
            request,
            self.template_name,
            {"asset": asset, "form": form, "form_title": "Tekil varlık çıkışı"},
        )

    def get(self, request, pk):
        asset = self._asset(pk)
        if asset.current_state != SerializedAsset.CurrentState.IN_STOCK:
            raise Http404("Serialized asset is not in stock")
        return self._render(request, asset, SerializedIssueForm(asset=asset))

    def post(self, request, pk):
        asset = self._asset(pk)
        form = SerializedIssueForm(request.POST, asset=asset)
        if not form.is_valid():
            return self._render(request, asset, form)
        try:
            result = issue_serialized(
                actor=request.user,
                operation_id=form.cleaned_data["operation_id"],
                serialized_asset_id=asset.pk,
                source_location_id=form.cleaned_data["source_location"].pk,
                condition_id=form.cleaned_data["condition"].pk,
                receiver_employee_id=form.cleaned_data["receiver_employee"].pk,
                production_line_id=form.cleaned_data["production_line"].pk,
                usage_location_text=form.cleaned_data["usage_location_text"],
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            attach_serialized_issue_validation_error(form, exc)
            return self._render(request, asset, form)

        if result.replayed:
            messages.info(request, "Bu tekil varlık çıkışı daha önce kaydedilmişti.")
        else:
            messages.success(request, "Tekil varlık çıkışı kaydedildi.")
        return redirect("inventory:issue-detail", pk=result.transaction.pk)


class SerializedReturnCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "inventory.return_stock"
    template_name = "inventory/serialized_return_form.html"

    def _objects(self, asset_pk, issue_line_pk):
        asset = get_object_or_404(
            SerializedAsset.objects.select_related(
                "material", "current_condition", "current_location"
            ),
            pk=asset_pk,
        )
        issue_line = get_object_or_404(
            InventoryTransactionLine.objects.select_related(
                "transaction__issue_context",
                "source_location",
                "condition",
                "material",
                "serialized_asset",
            ),
            pk=issue_line_pk,
            serialized_asset_id=asset.pk,
            transaction__transaction_type=InventoryTransaction.TransactionType.ISSUE,
            quantity__isnull=True,
            unit__isnull=True,
        )
        return asset, issue_line

    def _render(self, request, asset, issue_line, form):
        return render(
            request,
            self.template_name,
            {
                "asset": asset,
                "issue_line": issue_line,
                "issue_context": issue_line.transaction.issue_context,
                "form": form,
                "form_title": "Tekil varlık iadesi",
            },
        )

    def get(self, request, asset_pk, issue_line_pk):
        asset, issue_line = self._objects(asset_pk, issue_line_pk)
        if (
            asset.current_state != SerializedAsset.CurrentState.ISSUED
            or issue_line.return_lines.filter(
                transaction__transaction_type=InventoryTransaction.TransactionType.RETURN
            ).exists()
        ):
            raise Http404("Serialized issue is not returnable")
        return self._render(request, asset, issue_line, SerializedReturnForm())

    def post(self, request, asset_pk, issue_line_pk):
        asset, issue_line = self._objects(asset_pk, issue_line_pk)
        form = SerializedReturnForm(request.POST)
        if not form.is_valid():
            return self._render(request, asset, issue_line, form)
        try:
            result = return_serialized(
                actor=request.user,
                operation_id=form.cleaned_data["operation_id"],
                original_issue_line_id=issue_line.pk,
                serialized_asset_id=asset.pk,
                target_location_id=form.cleaned_data["target_location"].pk,
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            attach_serialized_return_validation_error(form, exc)
            return self._render(request, asset, issue_line, form)

        if result.replayed:
            messages.info(request, "Bu tekil varlık iadesi daha önce kaydedilmişti.")
        else:
            messages.success(request, "Tekil varlık iadesi kaydedildi.")
        return redirect("inventory:return-detail", pk=result.transaction.pk)


class SerializedTransferCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "inventory.transfer_stock"
    template_name = "inventory/serialized_transfer_form.html"

    def _asset(self, pk):
        return get_object_or_404(
            SerializedAsset.objects.select_related(
                "material", "current_location", "current_condition"
            ),
            pk=pk,
        )

    def _render(self, request, asset, form):
        return render(
            request,
            self.template_name,
            {"asset": asset, "form": form, "form_title": "Tekil varlık transferi"},
        )

    def get(self, request, pk):
        asset = self._asset(pk)
        if asset.current_state != SerializedAsset.CurrentState.IN_STOCK:
            raise Http404("Serialized asset is not in stock")
        return self._render(request, asset, SerializedTransferForm(asset=asset))

    def post(self, request, pk):
        asset = self._asset(pk)
        form = SerializedTransferForm(request.POST, asset=asset)
        if not form.is_valid():
            return self._render(request, asset, form)
        try:
            result = transfer_serialized(
                actor=request.user,
                operation_id=form.cleaned_data["operation_id"],
                serialized_asset_id=asset.pk,
                source_location_id=form.cleaned_data["source_location"].pk,
                target_location_id=form.cleaned_data["target_location"].pk,
                condition_id=form.cleaned_data["condition"].pk,
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            attach_serialized_transfer_validation_error(form, exc)
            return self._render(request, asset, form)

        if result.replayed:
            messages.info(request, "Bu tekil varlık transferi daha önce kaydedilmişti.")
        else:
            messages.success(request, "Tekil varlık transferi kaydedildi.")
        return redirect("inventory:transfer-detail", pk=result.transaction.pk)


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
                "lines__serialized_asset",
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
        if lines[0].serialized_asset_id:
            context["return_remaining_quantity"] = 0
            context["serialized_return_available"] = (
                lines[0].serialized_asset.current_state
                == SerializedAsset.CurrentState.ISSUED
                and not lines[0].return_lines.filter(
                    transaction__transaction_type=(
                        InventoryTransaction.TransactionType.RETURN
                    )
                ).exists()
            )
        else:
            returned_quantity = (
                lines[0]
                .return_lines.filter(
                    transaction__transaction_type=InventoryTransaction.TransactionType.RETURN
                )
                .aggregate(total=Sum("quantity"))["total"]
                or 0
            )
            context["return_remaining_quantity"] = lines[0].quantity - returned_quantity
        return context


class ReturnCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "inventory.return_stock"
    template_name = "inventory/return_form.html"

    def _render(self, request, form):
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "form_title": "Stok iadesi",
                "submit_label": "İadeyi kaydet",
            },
        )

    def get(self, request):
        initial = {}
        if request.GET.get("issue_line"):
            initial["original_issue_line"] = request.GET["issue_line"]
        return self._render(request, QuantityReturnForm(initial=initial))

    def post(self, request):
        form = QuantityReturnForm(request.POST)
        if not form.is_valid():
            return self._render(request, form)

        try:
            result = return_quantity(
                actor=request.user,
                operation_id=form.cleaned_data["operation_id"],
                original_issue_line_id=form.cleaned_data["original_issue_line"].pk,
                target_location_id=form.cleaned_data["target_location"].pk,
                quantity=form.cleaned_data["quantity"],
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            attach_return_validation_error(form, exc)
            return self._render(request, form)

        if result.replayed:
            messages.info(request, "Bu stok iadesi daha önce kaydedilmişti.")
        else:
            messages.success(request, "Stok iadesi kaydedildi.")
        return redirect("inventory:return-detail", pk=result.transaction.pk)


class ReturnDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = "inventory.return_stock"
    model = InventoryTransaction
    context_object_name = "return_transaction"
    template_name = "inventory/return_detail.html"
    http_method_names = ["get", "head"]

    def get_queryset(self):
        return_line_queryset = InventoryTransactionLine.objects.select_related(
            "material",
            "unit",
            "condition",
            "target_location",
            "original_issue_line__transaction__issue_context",
            "original_issue_line__source_location",
            "original_issue_line__serialized_asset",
            "serialized_asset",
        ).order_by("line_number")
        return (
            InventoryTransaction.objects.filter(
                transaction_type=InventoryTransaction.TransactionType.RETURN,
            )
            .select_related("acting_user")
            .prefetch_related(Prefetch("lines", queryset=return_line_queryset))
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lines = list(self.object.lines.all())
        if len(lines) != 1 or lines[0].original_issue_line_id is None:
            raise Http404("Return not found")
        line = lines[0]
        original_issue_line = line.original_issue_line
        if line.serialized_asset_id:
            context["is_serialized_return"] = True
        else:
            cumulative_returned = (
                original_issue_line.return_lines.filter(
                    transaction__transaction_type=InventoryTransaction.TransactionType.RETURN
                )
                .aggregate(total=Sum("quantity"))["total"]
                or 0
            )
            remaining_returnable = original_issue_line.quantity - cumulative_returned
            context["cumulative_returned"] = cumulative_returned
            context["remaining_returnable"] = remaining_returnable
        context.update(
            {
                "line": line,
                "original_issue_line": original_issue_line,
                "original_issue": original_issue_line.transaction,
                "issue_context": original_issue_line.transaction.issue_context,
            }
        )
        return context


class StockListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = STOCK_LIST_PERMISSION
    model = StockBalance
    context_object_name = "stock_balances"
    template_name = "inventory/stock_list.html"
    paginate_by = STOCK_LIST_PAGE_SIZE
    http_method_names = ["get", "head"]

    def get_queryset(self):
        return build_stock_balance_queryset(self.request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = stock_filter_params_from_request(self.request)
        context.update(params)
        context["location"] = (
            str(params["location_id"]) if params["location_id"] is not None else ""
        )
        context["condition"] = (
            str(params["condition_id"]) if params["condition_id"] is not None else ""
        )
        context.update(stock_list_filter_form_context())
        return context


class TransactionHistoryListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = TRANSACTION_HISTORY_PERMISSION
    model = InventoryTransaction
    context_object_name = "transactions"
    template_name = "inventory/transaction_history_list.html"
    paginate_by = TRANSACTION_HISTORY_PAGE_SIZE
    http_method_names = ["get", "head"]

    def get_queryset(self):
        return build_transaction_history_queryset(self.request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = filter_params_from_request(self.request)
        context.update(params)
        context["transaction_type"] = normalize_transaction_type_filter(
            params["transaction_type"]
        )
        context["transaction_type_raw"] = self.request.GET.get("transaction_type", "").strip()
        context["material"] = (
            str(params["material_id"]) if params["material_id"] is not None else ""
        )
        context["condition"] = (
            str(params["condition_id"]) if params["condition_id"] is not None else ""
        )
        context["location"] = (
            str(params["location_id"]) if params["location_id"] is not None else ""
        )
        context["actor"] = (
            str(params["actor_id"]) if params["actor_id"] is not None else ""
        )
        context["date_from"] = self.request.GET.get("date_from", "").strip()
        context["date_to"] = self.request.GET.get("date_to", "").strip()
        context.update(filter_form_context())
        return context


class TransactionHistoryDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = TRANSACTION_HISTORY_PERMISSION
    model = InventoryTransaction
    context_object_name = "transaction"
    template_name = "inventory/transaction_history_detail.html"
    http_method_names = ["get", "head"]

    def get_queryset(self):
        from inventory.transaction_history import LINE_PREFETCH

        return (
            InventoryTransaction.objects.filter(
                transaction_type__in=(
                    InventoryTransaction.TransactionType.RECEIPT,
                    InventoryTransaction.TransactionType.ISSUE,
                    InventoryTransaction.TransactionType.RETURN,
                    InventoryTransaction.TransactionType.TRANSFER,
                    InventoryTransaction.TransactionType.CONTROLLED_CORRECTION,
                    InventoryTransaction.TransactionType.COUNT_RECONCILIATION,
                    InventoryTransaction.TransactionType.INITIAL_BALANCE,
                )
            )
            .select_related("acting_user", "issue_context")
            .prefetch_related(LINE_PREFETCH)
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lines = list(self.object.lines.all())
        if not lines:
            raise Http404("Transaction not found")
        context["line"] = lines[0]
        context["lines"] = lines
        context["linked_correction_requests"] = self.object.correction_requests.select_related(
            "requester", "decided_by", "resulting_transaction"
        ).order_by("requested_at", "id")
        if self.object.transaction_type == InventoryTransaction.TransactionType.ISSUE:
            context["issue_context"] = self.object.issue_context
        elif self.object.transaction_type == InventoryTransaction.TransactionType.RETURN:
            context["original_issue_line"] = lines[0].original_issue_line
        elif (
            self.object.transaction_type
            == InventoryTransaction.TransactionType.CONTROLLED_CORRECTION
        ):
            context["corrected_line"] = lines[0].corrected_line
        return context


class TransferCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "inventory.transfer_stock"
    template_name = "inventory/transfer_form.html"

    def _render(self, request, form):
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "form_title": "Stok transferi",
                "submit_label": "Kaydet",
            },
        )

    def get(self, request):
        initial = {}
        for field_name in ("material", "source_location", "target_location", "condition"):
            value = request.GET.get(field_name, "").strip()
            if value:
                initial[field_name] = value
        return self._render(request, QuantityTransferForm(initial=initial))

    def post(self, request):
        form = QuantityTransferForm(request.POST)
        if not form.is_valid():
            return self._render(request, form)

        material = form.cleaned_data["material"]
        unit = form.cleaned_data.get("unit") or material.unit
        try:
            result = transfer_quantity(
                actor=request.user,
                operation_id=form.cleaned_data["operation_id"],
                material_id=material.pk,
                unit_id=unit.pk,
                condition_id=form.cleaned_data["condition"].pk,
                source_location_id=form.cleaned_data["source_location"].pk,
                target_location_id=form.cleaned_data["target_location"].pk,
                quantity=form.cleaned_data["quantity"],
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            attach_transfer_validation_error(form, exc)
            return self._render(request, form)

        if result.replayed:
            messages.info(request, "Bu stok transferi daha önce kaydedilmişti.")
        else:
            messages.success(request, "Stok transferi kaydedildi.")
        return redirect("inventory:transfer-detail", pk=result.transaction.pk)


class TransferDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = "inventory.transfer_stock"
    model = InventoryTransaction
    context_object_name = "transfer"
    template_name = "inventory/transfer_detail.html"
    http_method_names = ["get", "head"]

    def get_queryset(self):
        return (
            InventoryTransaction.objects.filter(
                transaction_type=InventoryTransaction.TransactionType.TRANSFER,
            )
            .select_related("acting_user")
            .prefetch_related(
                "lines__material__unit",
                "lines__serialized_asset",
                "lines__condition",
                "lines__source_location",
                "lines__target_location",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lines = list(self.object.lines.all())
        if len(lines) != 1:
            raise Http404("Transfer not found")
        context["line"] = lines[0]
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
                "lines__serialized_asset",
                "lines__condition",
                "lines__target_location",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lines = list(self.object.lines.all())
        if len(lines) != 1:
            raise Http404("Receipt not found")
        line = lines[0]
        context["line"] = line
        context["location_path"] = (
            location_path_label(line.target_location)
            if line.target_location_id
            else ""
        )
        context["resulting_balance"] = None
        if (
            line.serialized_asset_id is None
            and line.material_id
            and line.target_location_id
            and line.condition_id
        ):
            context["resulting_balance"] = (
                StockBalance.objects.filter(
                    material_id=line.material_id,
                    location_id=line.target_location_id,
                    condition_id=line.condition_id,
                )
                .select_related("location", "condition")
                .first()
            )
        return context
