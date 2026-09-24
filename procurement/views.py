from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from inventory.forms import (
    QuantityReceiptForm,
    SerializedReceiptForm,
    attach_receipt_validation_error,
    attach_serialized_receipt_validation_error,
    receipt_metadata_from_cleaned,
)
from locations.display import location_path_label
from procurement.forms import MaterialLinkForm, PurchaseRequestForm, PurchaseRequestLineForm
from procurement.models import FulfillmentState, PurchaseRequest, PurchaseRequestLine
from procurement.services import (
    ADD_PERMISSION,
    CHANGE_PERMISSION,
    VIEW_PERMISSION,
    add_purchase_request_line,
    arrival_bounds,
    create_purchase_request,
    header_fulfillment_state,
    link_line_material,
    receive_quantity_for_line,
    receive_serialized_for_line,
    request_is_late,
    search_purchase_requests,
    update_purchase_request,
    update_purchase_request_line,
)


def _line_rows(purchase_request):
    rows = []
    states = []
    for line in purchase_request.lines.all():
        state = line.fulfillment_state()
        states.append(state)
        first, latest = arrival_bounds(line)
        rows.append(
            {
                "line": line,
                "state": state,
                "label": FulfillmentState.LABELS[state],
                "received": line.received_quantity(),
                "remaining": line.remaining_quantity(),
                "over": line.over_received_quantity(),
                "total": line.derived_total_price(),
                "first_arrival": first,
                "latest_arrival": latest,
            }
        )
    return rows, header_fulfillment_state(states)


class PurchaseRequestListView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = VIEW_PERMISSION
    template_name = "procurement/request_list.html"

    def get(self, request):
        query = request.GET.get("q", "")
        queryset = PurchaseRequest.objects.select_related("created_by").prefetch_related(
            "lines__material",
            "lines__receipts",
        )
        queryset = search_purchase_requests(queryset, query).order_by("-request_date", "request_no")
        rows = []
        for purchase_request in queryset[:200]:
            line_rows, state = _line_rows(purchase_request)
            rows.append(
                {
                    "request": purchase_request,
                    "line_count": len(line_rows),
                    "label": FulfillmentState.LABELS[state],
                    "late": request_is_late(purchase_request),
                }
            )
        return render(
            request,
            self.template_name,
            {"rows": rows, "query": query, "can_add": request.user.has_perm(ADD_PERMISSION)},
        )


class PurchaseRequestCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = ADD_PERMISSION
    template_name = "procurement/request_form.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {"form": PurchaseRequestForm(), "form_title": "Yeni talep"},
        )

    def post(self, request):
        form = PurchaseRequestForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "form_title": "Yeni talep"})
        try:
            result = create_purchase_request(actor=request.user, **form.cleaned_data)
        except PermissionDenied:
            raise
        except ValidationError as exc:
            form.add_error(None, exc.messages[0] if exc.messages else str(exc))
            return render(request, self.template_name, {"form": form, "form_title": "Yeni talep"})
        messages.success(request, "Talep kaydedildi. Stok oluşmadı.")
        return redirect("procurement:request-detail", pk=result.purchase_request.pk)


class PurchaseRequestUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = CHANGE_PERMISSION
    template_name = "procurement/request_form.html"

    def get(self, request, pk):
        purchase_request = get_object_or_404(PurchaseRequest, pk=pk)
        form = PurchaseRequestForm(
            initial={
                "request_no": purchase_request.request_no,
                "request_date": purchase_request.request_date,
                "approval_date": purchase_request.approval_date,
                "note": purchase_request.note,
            }
        )
        return render(
            request,
            self.template_name,
            {"form": form, "form_title": "Talep düzenle", "purchase_request": purchase_request},
        )

    def post(self, request, pk):
        purchase_request = get_object_or_404(PurchaseRequest, pk=pk)
        form = PurchaseRequestForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"form": form, "form_title": "Talep düzenle", "purchase_request": purchase_request},
            )
        try:
            update_purchase_request(
                actor=request.user,
                purchase_request_id=purchase_request.pk,
                **form.cleaned_data,
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            form.add_error(None, exc.messages[0] if exc.messages else str(exc))
            return render(
                request,
                self.template_name,
                {"form": form, "form_title": "Talep düzenle", "purchase_request": purchase_request},
            )
        messages.success(request, "Talep güncellendi.")
        return redirect("procurement:request-detail", pk=purchase_request.pk)


class PurchaseRequestDetailView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = VIEW_PERMISSION
    template_name = "procurement/request_detail.html"

    def get(self, request, pk):
        purchase_request = get_object_or_404(
            PurchaseRequest.objects.prefetch_related(
                Prefetch(
                    "lines",
                    queryset=PurchaseRequestLine.objects.select_related(
                        "material", "unit"
                    ).prefetch_related("receipts", "receipts__created_by"),
                )
            ),
            pk=pk,
        )
        rows, state = _line_rows(purchase_request)
        return render(
            request,
            self.template_name,
            {
                "purchase_request": purchase_request,
                "rows": rows,
                "status_label": FulfillmentState.LABELS[state],
                "late": request_is_late(purchase_request),
                "can_change": request.user.has_perm(CHANGE_PERMISSION),
                "can_receive": request.user.has_perm("inventory.receive_stock"),
            },
        )


class PurchaseRequestLineCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = CHANGE_PERMISSION
    template_name = "procurement/line_form.html"

    def get(self, request, pk):
        purchase_request = get_object_or_404(PurchaseRequest, pk=pk)
        return render(
            request,
            self.template_name,
            {
                "form": PurchaseRequestLineForm(),
                "form_title": "Talep kalemi ekle",
                "purchase_request": purchase_request,
            },
        )

    def post(self, request, pk):
        purchase_request = get_object_or_404(PurchaseRequest, pk=pk)
        form = PurchaseRequestLineForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "form_title": "Talep kalemi ekle",
                    "purchase_request": purchase_request,
                },
            )
        cleaned = form.cleaned_data
        try:
            add_purchase_request_line(
                actor=request.user,
                purchase_request_id=purchase_request.pk,
                requested_description=cleaned["requested_description"],
                unit_id=cleaned["unit"].pk,
                requested_quantity=cleaned["requested_quantity"],
                material_id=cleaned["material"].pk if cleaned["material"] else None,
                lead_time_days=cleaned["lead_time_days"],
                expected_arrival_date=cleaned["expected_arrival_date"],
                supplier_name=cleaned["supplier_name"],
                unit_price=cleaned["unit_price"],
                currency_code=cleaned["currency_code"],
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            form.add_error(None, exc.messages[0] if exc.messages else str(exc))
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "form_title": "Talep kalemi ekle",
                    "purchase_request": purchase_request,
                },
            )
        messages.success(request, "Talep kalemi eklendi. Stok oluşmadı.")
        return redirect("procurement:request-detail", pk=purchase_request.pk)


class PurchaseRequestLineUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = CHANGE_PERMISSION
    template_name = "procurement/line_form.html"

    def _line(self, pk, line_pk):
        return get_object_or_404(
            PurchaseRequestLine.objects.select_related("purchase_request", "material", "unit"),
            pk=line_pk,
            purchase_request_id=pk,
        )

    def _initial(self, line):
        return {
            "requested_description": line.requested_description,
            "material": line.material_id,
            "unit": line.unit_id,
            "requested_quantity": line.requested_quantity,
            "lead_time_days": line.lead_time_days,
            "expected_arrival_date": line.expected_arrival_date,
            "supplier_name": line.supplier_name or "",
            "unit_price": line.unit_price,
            "currency_code": line.currency_code or "",
        }

    def get(self, request, pk, line_pk):
        line = self._line(pk, line_pk)
        return render(
            request,
            self.template_name,
            {
                "form": PurchaseRequestLineForm(initial=self._initial(line)),
                "form_title": "Talep kalemini düzenle",
                "purchase_request": line.purchase_request,
                "line": line,
            },
        )

    def post(self, request, pk, line_pk):
        line = self._line(pk, line_pk)
        form = PurchaseRequestLineForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "form_title": "Talep kalemini düzenle",
                    "purchase_request": line.purchase_request,
                    "line": line,
                },
            )
        cleaned = form.cleaned_data
        try:
            update_purchase_request_line(
                actor=request.user,
                line_id=line.pk,
                requested_description=cleaned["requested_description"],
                unit_id=cleaned["unit"].pk,
                requested_quantity=cleaned["requested_quantity"],
                material_id=cleaned["material"].pk if cleaned["material"] else None,
                lead_time_days=cleaned["lead_time_days"],
                expected_arrival_date=cleaned["expected_arrival_date"],
                supplier_name=cleaned["supplier_name"],
                unit_price=cleaned["unit_price"],
                currency_code=cleaned["currency_code"],
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            form.add_error(None, exc.messages[0] if exc.messages else str(exc))
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "form_title": "Talep kalemini düzenle",
                    "purchase_request": line.purchase_request,
                    "line": line,
                },
            )
        messages.success(request, "Talep kalemi güncellendi.")
        return redirect("procurement:request-detail", pk=line.purchase_request_id)


class PurchaseRequestLineLinkView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = CHANGE_PERMISSION
    template_name = "procurement/link_form.html"

    def get(self, request, pk, line_pk):
        line = get_object_or_404(
            PurchaseRequestLine.objects.select_related("purchase_request", "unit"),
            pk=line_pk,
            purchase_request_id=pk,
        )
        return render(
            request,
            self.template_name,
            {
                "form": MaterialLinkForm(unit_id=line.unit_id),
                "line": line,
                "purchase_request": line.purchase_request,
            },
        )

    def post(self, request, pk, line_pk):
        line = get_object_or_404(
            PurchaseRequestLine.objects.select_related("purchase_request", "unit"),
            pk=line_pk,
            purchase_request_id=pk,
        )
        form = MaterialLinkForm(request.POST, unit_id=line.unit_id)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"form": form, "line": line, "purchase_request": line.purchase_request},
            )
        description = line.requested_description
        try:
            link_line_material(
                actor=request.user,
                line_id=line.pk,
                material_id=form.cleaned_data["material"].pk,
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            form.add_error(None, exc.messages[0] if exc.messages else str(exc))
            return render(
                request,
                self.template_name,
                {"form": form, "line": line, "purchase_request": line.purchase_request},
            )
        line.refresh_from_db()
        if line.requested_description != description:
            messages.error(request, "Talep açıklaması korunamadı.")
        else:
            messages.success(request, "Kalem malzemeye bağlandı. Açıklama aynı kaldı.")
        return redirect("procurement:request-detail", pk=pk)


class PurchaseRequestLineReceiveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "inventory.receive_stock"
    template_name = "inventory/receipt_form.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.has_perm(CHANGE_PERMISSION):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def _line(self, pk, line_pk):
        return get_object_or_404(
            PurchaseRequestLine.objects.select_related(
                "purchase_request", "material", "unit"
            ),
            pk=line_pk,
            purchase_request_id=pk,
            material__isnull=False,
        )

    def get(self, request, pk, line_pk):
        line = self._line(pk, line_pk)
        if line.material.tracking_mode != "QUANTITY":
            return redirect(
                "procurement:line-receive-serialized",
                pk=pk,
                line_pk=line.pk,
            )
        remaining = line.remaining_quantity()
        initial = {"material": line.material_id}
        if remaining > 0:
            initial["quantity"] = remaining
        if line.supplier_name:
            initial["supplier_name"] = line.supplier_name
        return self._render(request, line, QuantityReceiptForm(initial=initial))

    def post(self, request, pk, line_pk):
        line = self._line(pk, line_pk)
        if line.material.tracking_mode != "QUANTITY":
            return redirect(
                "procurement:line-receive-serialized",
                pk=pk,
                line_pk=line.pk,
            )
        form = QuantityReceiptForm(request.POST)
        if not form.is_valid():
            return self._render(request, line, form)
        if form.cleaned_data["material"].pk != line.material_id:
            form.add_error("material", "Stok girişi bu talep kaleminin malzemesi olmalıdır.")
            return self._render(request, line, form)
        if request.POST.get("intent") == "edit":
            return self._render(request, line, form)
        if request.POST.get("confirm") != "1":
            return self._render(request, line, form, preview=self._preview(form))
        try:
            result = receive_quantity_for_line(
                actor=request.user,
                line_id=line.pk,
                operation_id=form.cleaned_data["operation_id"],
                condition_id=form.cleaned_data["condition"].pk,
                target_location_id=form.cleaned_data["target_location"].pk,
                quantity=form.cleaned_data["quantity"],
                receipt_metadata=receipt_metadata_from_cleaned(
                    form.cleaned_data,
                    include_packaging=True,
                ),
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            attach_receipt_validation_error(form, exc)
            if not form.errors:
                form.add_error(None, exc.messages[0] if getattr(exc, "messages", None) else str(exc))
            return self._render(request, line, form)
        if result.replayed:
            messages.info(request, "Bu talep karşılama kaydı daha önce işlenmişti.")
        else:
            messages.success(request, "Mal kabul talep kalemine bağlandı.")
        return redirect("procurement:request-detail", pk=pk)

    def _preview(self, form):
        material = form.cleaned_data["material"]
        location = form.cleaned_data["target_location"]
        return {
            "material": material,
            "location": location,
            "location_path": location_path_label(location),
            "condition": form.cleaned_data["condition"],
            "quantity": form.cleaned_data["quantity"],
            "unit": material.unit,
            "usage_place": form.cleaned_data["usage_place"],
            "arrived_on": form.cleaned_data["arrived_on"],
            "supplier_name": form.cleaned_data.get("supplier_name") or "",
            "package_count": form.cleaned_data.get("package_count"),
            "package_label": form.cleaned_data.get("package_label") or "",
            "contents_per_package": form.cleaned_data.get("contents_per_package"),
        }

    def _render(self, request, line, form, *, preview=None):
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "form_title": "Talep için mal kabul",
                "submit_label": "Girişi kaydet" if preview else "Önizle",
                "preview": preview,
                "purchase_request_line": line,
                "cancel_url_name": "procurement:request-detail",
                "cancel_pk": line.purchase_request_id,
            },
        )


class PurchaseRequestLineSerializedReceiveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "inventory.receive_stock"
    template_name = "inventory/serialized_receipt_form.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.has_perm(CHANGE_PERMISSION):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def _line(self, pk, line_pk):
        return get_object_or_404(
            PurchaseRequestLine.objects.select_related("purchase_request", "material", "unit"),
            pk=line_pk,
            purchase_request_id=pk,
            material__tracking_mode="SERIALIZED",
        )

    def get(self, request, pk, line_pk):
        line = self._line(pk, line_pk)
        return render(
            request,
            self.template_name,
            {
                "form": SerializedReceiptForm(
                    initial={
                        "material": line.material_id,
                        "supplier_name": line.supplier_name or "",
                    }
                ),
                "form_title": "Talep için tekil varlık girişi",
                "submit_label": "Kaydet",
                "purchase_request_line": line,
            },
        )

    def post(self, request, pk, line_pk):
        line = self._line(pk, line_pk)
        form = SerializedReceiptForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "form_title": "Talep için tekil varlık girişi",
                    "submit_label": "Kaydet",
                    "purchase_request_line": line,
                },
            )
        if form.cleaned_data["material"].pk != line.material_id:
            form.add_error("material", "Stok girişi bu talep kaleminin malzemesi olmalıdır.")
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "form_title": "Talep için tekil varlık girişi",
                    "submit_label": "Kaydet",
                    "purchase_request_line": line,
                },
            )
        try:
            result = receive_serialized_for_line(
                actor=request.user,
                line_id=line.pk,
                operation_id=form.cleaned_data["operation_id"],
                internal_asset_code=form.cleaned_data["internal_asset_code"],
                serial_number=form.cleaned_data["serial_number"],
                condition_id=form.cleaned_data["condition"].pk,
                target_location_id=form.cleaned_data["target_location"].pk,
                receipt_metadata=receipt_metadata_from_cleaned(
                    form.cleaned_data,
                    include_packaging=False,
                ),
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            attach_serialized_receipt_validation_error(form, exc)
            if not form.errors:
                form.add_error(None, exc.messages[0] if getattr(exc, "messages", None) else str(exc))
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "form_title": "Talep için tekil varlık girişi",
                    "submit_label": "Kaydet",
                    "purchase_request_line": line,
                },
            )
        if result.replayed:
            messages.info(request, "Bu tekil varlık talebe daha önce bağlanmıştı.")
        else:
            messages.success(request, "Tekil varlık girişi talep kalemine bağlandı.")
        return redirect("procurement:request-detail", pk=pk)
