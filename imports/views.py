from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from counting.models import PhysicalCountSession
from imports.forms import BaselineEstablishForm, BaselinePrepareForm
from imports.models import InventoryBaseline
from imports.readiness import collect_baseline_readiness
from imports.services import establish_inventory_baseline, prepare_inventory_baseline

ESTABLISH_BASELINE_PERMISSION = "imports.establish_baseline"


def _message_for_validation(exc: ValidationError) -> str:
    if hasattr(exc, "messages") and exc.messages:
        return " ".join(str(message) for message in exc.messages)
    return str(exc)


def _attach_validation_error(form, exc: ValidationError) -> None:
    if hasattr(exc, "error_dict"):
        for field, errors in exc.error_dict.items():
            if field == "__all__":
                for error in errors:
                    form.add_error(None, error)
            elif field in form.fields:
                for error in errors:
                    form.add_error(field, error)
            else:
                for error in errors:
                    form.add_error(None, error)
    else:
        form.add_error(None, exc)


class InventoryBaselineListView(
    LoginRequiredMixin, PermissionRequiredMixin, ListView
):
    permission_required = ESTABLISH_BASELINE_PERMISSION
    model = InventoryBaseline
    context_object_name = "baselines"
    template_name = "imports/baseline_list.html"
    paginate_by = 50
    http_method_names = ["get", "head"]

    def get_queryset(self):
        return InventoryBaseline.objects.select_related(
            "created_by", "established_by"
        ).order_by("-created_at", "-id")


class InventoryBaselineCreateView(
    LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = ESTABLISH_BASELINE_PERMISSION
    template_name = "imports/baseline_form.html"

    def _render(self, request, form):
        return render(request, self.template_name, {"form": form})

    def get(self, request):
        initial = {}
        session_id = request.GET.get("session", "").strip()
        if session_id:
            initial["sessions"] = [session_id]
        return self._render(request, BaselinePrepareForm(initial=initial))

    def post(self, request):
        form = BaselinePrepareForm(request.POST)
        if not form.is_valid():
            return self._render(request, form)
        try:
            result = prepare_inventory_baseline(
                actor=request.user,
                session_ids=[session.pk for session in form.cleaned_data["sessions"]],
                reference=form.cleaned_data.get("reference") or None,
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self._render(request, form)
        messages.success(request, "Kesim hazırlandı. Henüz stok yetkili değildir.")
        return redirect("imports:baseline-detail", pk=result.baseline.pk)


class InventoryBaselineDetailView(
    LoginRequiredMixin, PermissionRequiredMixin, DetailView
):
    permission_required = ESTABLISH_BASELINE_PERMISSION
    model = InventoryBaseline
    context_object_name = "baseline"
    template_name = "imports/baseline_detail.html"
    http_method_names = ["get", "head"]

    def get_queryset(self):
        return InventoryBaseline.objects.select_related(
            "created_by", "established_by"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        baseline = self.object
        sessions = [
            link.physical_count_session
            for link in baseline.count_session_links.select_related(
                "physical_count_session__scope_location",
                "physical_count_session__completed_by_user",
            ).order_by("physical_count_session__reference_number")
        ]
        context["sessions"] = sessions
        context["transaction_links"] = list(
            baseline.transaction_links.select_related(
                "inventory_transaction", "physical_count_session"
            ).order_by("scope_key")
        )
        context["readiness_issues"] = collect_baseline_readiness(
            sessions,
            actor=self.request.user,
            current_baseline=baseline,
        )
        context["establish_form"] = BaselineEstablishForm()
        context["can_establish"] = (
            baseline.status == InventoryBaseline.Status.PREPARED
        )
        return context


class InventoryBaselineEstablishView(
    LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = ESTABLISH_BASELINE_PERMISSION
    http_method_names = ["post"]

    def post(self, request, pk):
        baseline = get_object_or_404(InventoryBaseline, pk=pk)
        form = BaselineEstablishForm(request.POST)
        if not form.is_valid():
            messages.error(
                request,
                "Kesim açıklaması 10 ile 2000 karakter arasında olmalıdır.",
            )
            return redirect("imports:baseline-detail", pk=baseline.pk)
        try:
            result = establish_inventory_baseline(
                actor=request.user,
                baseline_id=baseline.pk,
                operation_id=form.cleaned_data["operation_id"],
                explanation=form.cleaned_data["explanation"],
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            messages.error(request, _message_for_validation(exc))
            return redirect("imports:baseline-detail", pk=baseline.pk)
        if result.replayed:
            messages.info(request, "Bu kesim daha önce uygulanmıştı.")
        else:
            messages.success(
                request,
                "Kesim uygulandı. Açılış bakiyesi kontrollü INITIAL_BALANCE ile oluştu.",
            )
        return redirect("imports:baseline-detail", pk=baseline.pk)
