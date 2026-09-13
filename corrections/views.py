from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from corrections.forms import CorrectionRejectionForm, CorrectionRequestForm
from corrections.models import CorrectionRequest
from corrections.services import (
    approve_correction_request,
    create_correction_request,
    reject_correction_request,
)
from inventory.models import InventoryTransaction


def _message_for_validation(exc):
    if hasattr(exc, "messages") and exc.messages:
        return " ".join(exc.messages)
    return str(exc)


class CorrectionRequestCreateView(
    LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = (
        "corrections.add_correctionrequest",
        "inventory.view_inventorytransaction",
    )
    template_name = "corrections/request_form.html"

    def _transaction(self, transaction_pk):
        try:
            return InventoryTransaction.objects.prefetch_related("lines").get(
                pk=transaction_pk
            )
        except InventoryTransaction.DoesNotExist as exc:
            raise Http404("Transaction not found") from exc

    def _render(self, request, transaction_record, form):
        return render(
            request,
            self.template_name,
            {"form": form, "original_transaction": transaction_record},
        )

    def get(self, request, transaction_pk):
        transaction_record = self._transaction(transaction_pk)
        initial = {}
        line_id = request.GET.get("line")
        if line_id:
            initial["original_line"] = line_id
        return self._render(
            request,
            transaction_record,
            CorrectionRequestForm(
                original_transaction=transaction_record, initial=initial
            ),
        )

    def post(self, request, transaction_pk):
        transaction_record = self._transaction(transaction_pk)
        form = CorrectionRequestForm(
            request.POST, original_transaction=transaction_record
        )
        if not form.is_valid():
            return self._render(request, transaction_record, form)
        try:
            request_record = create_correction_request(
                actor=request.user,
                original_transaction_id=transaction_record.pk,
                original_line_id=form.cleaned_data["original_line"].pk,
                original_location_id=form.cleaned_data["original_location"].pk,
                explanation=form.cleaned_data["explanation"],
                effect_type=form.cleaned_data["effect_type"],
                quantity_effect=form.cleaned_data["quantity_effect"],
                corrected_material_id=(
                    form.cleaned_data["corrected_material"].pk
                    if form.cleaned_data.get("corrected_material")
                    else None
                ),
                corrected_location_id=(
                    form.cleaned_data["corrected_location"].pk
                    if form.cleaned_data.get("corrected_location")
                    else None
                ),
                corrected_condition_id=(
                    form.cleaned_data["corrected_condition"].pk
                    if form.cleaned_data.get("corrected_condition")
                    else None
                ),
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            form.add_error(None, exc)
            return self._render(request, transaction_record, form)
        messages.success(request, "Düzeltme talebi oluşturuldu.")
        return redirect("corrections:request-detail", pk=request_record.pk)


class CorrectionRequestListView(
    LoginRequiredMixin, PermissionRequiredMixin, ListView
):
    permission_required = "corrections.view_correctionrequest"
    model = CorrectionRequest
    context_object_name = "correction_requests"
    template_name = "corrections/request_list.html"
    paginate_by = 50

    def get_queryset(self):
        queryset = CorrectionRequest.objects.select_related(
            "original_transaction", "original_line__material", "requester", "decided_by"
        ).order_by("-requested_at", "-id")
        status = self.request.GET.get("status", "PENDING")
        if status in dict(CorrectionRequest.Status.choices):
            queryset = queryset.filter(status=status)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        status = self.request.GET.get("status", "PENDING")
        context["status_filter"] = (
            status if status in dict(CorrectionRequest.Status.choices) else ""
        )
        context["status_choices"] = CorrectionRequest.Status.choices
        return context


class CorrectionRequestDetailView(
    LoginRequiredMixin, PermissionRequiredMixin, DetailView
):
    permission_required = "corrections.view_correctionrequest"
    model = CorrectionRequest
    context_object_name = "correction_request"
    template_name = "corrections/request_detail.html"

    def get_queryset(self):
        return CorrectionRequest.objects.select_related(
            "original_transaction",
            "original_line__material__unit",
            "original_line__condition",
            "original_location",
            "requester",
            "decided_by",
            "resulting_transaction",
            "corrected_material__unit",
            "corrected_location",
            "corrected_condition",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["rejection_form"] = CorrectionRejectionForm()
        return context


class CorrectionApproveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "corrections.decide_correctionrequest"
    http_method_names = ["post"]

    def post(self, request, pk):
        try:
            result = approve_correction_request(
                actor=request.user, correction_request_id=pk
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            messages.error(request, _message_for_validation(exc))
            return redirect("corrections:request-detail", pk=pk)
        if result.replayed:
            messages.info(request, "Bu düzeltme talebi daha önce onaylanmıştı.")
        else:
            messages.success(request, "Düzeltme talebi onaylandı.")
        return redirect("corrections:request-detail", pk=pk)


class CorrectionRejectView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "corrections.decide_correctionrequest"
    http_method_names = ["post"]

    def post(self, request, pk):
        form = CorrectionRejectionForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Ret gerekçesi en fazla 2000 karakter olabilir.")
            return redirect("corrections:request-detail", pk=pk)
        try:
            reject_correction_request(
                actor=request.user,
                correction_request_id=pk,
                rejection_reason=form.cleaned_data["rejection_reason"],
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            messages.error(request, _message_for_validation(exc))
            return redirect("corrections:request-detail", pk=pk)
        messages.success(request, "Düzeltme talebi reddedildi.")
        return redirect("corrections:request-detail", pk=pk)
