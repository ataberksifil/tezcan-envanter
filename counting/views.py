from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from catalog.models import Material
from counting.forms import (
    CandidateSerializedForm,
    PhysicalCountSessionCreateForm,
    QuantityCountLineFormSet,
    QuantityDiscrepancyApprovalForm,
    QuantityDiscrepancyRejectionForm,
    SerializedIdentityLookupForm,
    SerializedObserveForm,
    UnexpectedQuantityForm,
    counted_at_token,
)
from counting.models import (
    PhysicalCountQuantityLine,
    PhysicalCountQuantityRejection,
    PhysicalCountSession,
)
from counting.presentation import (
    ADD_COUNT_PERMISSION,
    CHANGE_COUNT_PERMISSION,
    DECIDE_COUNT_PERMISSION,
    VIEW_COUNT_PERMISSION,
    can_see_expected,
    editable_quantity_lines,
    open_sessions_for_warning,
    scope_stock_location_queryset,
    session_action_flags,
    session_progress,
)
from counting.services import (
    add_candidate_serialized_count,
    add_unexpected_quantity_count,
    approve_quantity_discrepancy,
    complete_physical_count,
    create_physical_count_session,
    mark_quantity_line_not_counted,
    mark_serialized_asset_missing,
    mark_serialized_line_not_counted,
    record_quantity_count,
    record_serialized_asset_count,
    reject_quantity_discrepancy,
    start_physical_count_session,
)
from identification.codec import (
    IdentityCodecError,
    IdentityEntityType,
    decode_payload,
)
from inventory.models import SerializedAsset
from locations.models import Location


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


def _session_or_404(pk):
    return get_object_or_404(
        PhysicalCountSession.objects.select_related(
            "scope_location",
            "started_by_user",
            "completed_by_user",
        ),
        pk=pk,
    )


def _require_started(session):
    if session.status != PhysicalCountSession.Status.STARTED:
        raise ValidationError("Bu işlem yalnız başlatılmış sayım oturumunda yapılabilir.")


def _paired_quantity_forms(formset, rows):
    paired = []
    for form, line in zip(formset.forms, rows):
        paired.append(
            {
                "form": form,
                "line": line,
                "can_mark_not_counted": line.expected_quantity != 0,
            }
        )
    return paired


class PhysicalCountSessionListView(
    LoginRequiredMixin, PermissionRequiredMixin, ListView
):
    permission_required = VIEW_COUNT_PERMISSION
    context_object_name = "sessions"
    template_name = "counting/session_list.html"
    paginate_by = 50
    http_method_names = ["get", "head"]

    def get_queryset(self):
        queryset = (
            PhysicalCountSession.objects.select_related(
                "scope_location",
                "started_by_user",
                "completed_by_user",
            )
            .annotate(
                quantity_line_count=Count("quantity_lines", distinct=True),
                serialized_line_count=Count("serialized_lines", distinct=True),
                pending_quantity_count=Count(
                    "quantity_lines",
                    filter=Q(
                        quantity_lines__resolution_status=(
                            PhysicalCountQuantityLine.ResolutionStatus.PENDING_COUNT
                        )
                    ),
                    distinct=True,
                ),
                pending_approval_count=Count(
                    "quantity_lines",
                    filter=Q(
                        quantity_lines__resolution_status=(
                            PhysicalCountQuantityLine.ResolutionStatus.PENDING_APPROVAL
                        )
                    ),
                    distinct=True,
                ),
            )
            .order_by("-created_at", "-id")
        )
        status = self.request.GET.get("status", "")
        if status in dict(PhysicalCountSession.Status.choices):
            queryset = queryset.filter(status=status)
        recon = self.request.GET.get("reconciliation", "")
        if recon in dict(PhysicalCountSession.ReconciliationStatus.choices):
            queryset = queryset.filter(reconciliation_status=recon)
        candidate = self.request.GET.get("candidate", "")
        if candidate == "routine":
            queryset = queryset.filter(baseline_candidate=False)
        elif candidate == "baseline":
            queryset = queryset.filter(baseline_candidate=True)
        location_id = self.request.GET.get("location", "").strip()
        if location_id:
            queryset = queryset.filter(scope_location_id=location_id)
        if self.request.GET.get("mine", "") == "1":
            user = self.request.user
            queryset = queryset.filter(
                Q(started_by_user=user)
                | Q(completed_by_user=user)
                | Q(quantity_lines__counted_by_user=user)
                | Q(serialized_lines__counted_by_user=user)
            ).distinct()
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status_filter"] = self.request.GET.get("status", "")
        context["reconciliation_filter"] = self.request.GET.get("reconciliation", "")
        context["candidate_filter"] = self.request.GET.get("candidate", "")
        context["location_filter"] = self.request.GET.get("location", "")
        context["mine_filter"] = self.request.GET.get("mine", "")
        context["status_choices"] = PhysicalCountSession.Status.choices
        context["reconciliation_choices"] = (
            PhysicalCountSession.ReconciliationStatus.choices
        )
        context["filter_locations"] = Location.objects.order_by("code", "id")
        context["can_create"] = self.request.user.has_perm(ADD_COUNT_PERMISSION)
        context["can_establish"] = self.request.user.has_perm(
            "imports.establish_baseline"
        )
        return context


class PhysicalCountSessionCreateView(
    LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = ADD_COUNT_PERMISSION
    template_name = "counting/session_form.html"

    def _render(self, request, form):
        return render(
            request,
            self.template_name,
            {"form": form, "open_sessions": open_sessions_for_warning()},
        )

    def get(self, request):
        return self._render(request, PhysicalCountSessionCreateForm())

    def post(self, request):
        form = PhysicalCountSessionCreateForm(request.POST)
        if not form.is_valid():
            return self._render(request, form)
        try:
            session = create_physical_count_session(
                actor=request.user,
                reference_number=form.cleaned_data["reference_number"],
                scope_location_id=form.cleaned_data["scope_location"].pk,
                baseline_candidate=form.cleaned_data["baseline_candidate"],
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self._render(request, form)
        messages.success(request, "Sayım oturumu taslak olarak oluşturuldu.")
        return redirect("counting:session-detail", pk=session.pk)


class PhysicalCountSessionDetailView(
    LoginRequiredMixin, PermissionRequiredMixin, DetailView
):
    permission_required = VIEW_COUNT_PERMISSION
    model = PhysicalCountSession
    context_object_name = "session"
    template_name = "counting/session_detail.html"
    http_method_names = ["get", "head"]

    def get_queryset(self):
        return PhysicalCountSession.objects.select_related(
            "scope_location",
            "started_by_user",
            "completed_by_user",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        session = self.object
        show_expected = can_see_expected(self.request.user)
        context["quantity_lines"] = list(
            session.quantity_lines.select_related(
                "material__unit",
                "location",
                "condition",
                "counted_by_user",
                "approved_by_user",
                "reconciliation_transaction",
            ).order_by(
                "location__code",
                "material__material_code",
                "condition__sort_order",
                "id",
            )
        )
        context["serialized_lines"] = list(
            session.serialized_lines.select_related(
                "material",
                "serialized_asset",
                "expected_location",
                "expected_condition",
                "observed_location",
                "observed_condition",
                "counted_by_user",
            ).order_by("internal_asset_code", "id")
        )
        context["progress"] = session_progress(session)
        context["actions"] = session_action_flags(self.request.user, session)
        context["show_expected"] = show_expected
        context["rejections"] = []
        if show_expected:
            context["rejections"] = (
                PhysicalCountQuantityRejection.objects.filter(line__session=session)
                .select_related("line", "rejected_by_user", "counted_by_user")
                .order_by("-rejected_at")
            )
        context["baseline_link"] = session.baseline_links.select_related(
            "inventory_baseline"
        ).first()
        context["baseline_result_link"] = session.baseline_result_links.select_related(
            "inventory_transaction", "inventory_baseline"
        ).first()
        return context


class PhysicalCountSessionStartView(
    LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = CHANGE_COUNT_PERMISSION

    def get(self, request, pk):
        session = _session_or_404(pk)
        if session.status != PhysicalCountSession.Status.DRAFT:
            messages.error(request, "Yalnız taslak sayım oturumu başlatılabilir.")
            return redirect("counting:session-detail", pk=session.pk)
        return render(
            request,
            "counting/session_start_confirm.html",
            {"session": session},
        )

    def post(self, request, pk):
        session = _session_or_404(pk)
        try:
            start_physical_count_session(actor=request.user, session_id=session.pk)
        except PermissionDenied:
            raise
        except ValidationError as exc:
            messages.error(request, _message_for_validation(exc))
            return redirect("counting:session-detail", pk=session.pk)
        messages.success(
            request,
            "Sayım başlatıldı. Beklenen snapshot alındı; stok hareketleri durdurulmadı.",
        )
        return redirect("counting:session-detail", pk=session.pk)


class QuantityCountView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = CHANGE_COUNT_PERMISSION
    template_name = "counting/quantity_count.html"

    def _formset(self, session, data=None):
        rows = list(editable_quantity_lines(session))
        initial = [
            {
                "line_id": line.pk,
                "counted_at_token": counted_at_token(line.counted_at),
                "counted_quantity": (
                    line.counted_quantity
                    if line.resolution_status
                    != PhysicalCountQuantityLine.ResolutionStatus.NOT_COUNTED
                    else None
                ),
            }
            for line in rows
        ]
        formset = QuantityCountLineFormSet(data=data, initial=initial, prefix="qty")
        return formset, rows

    def _render(self, request, session, formset, rows):
        return render(
            request,
            self.template_name,
            {
                "session": session,
                "formset": formset,
                "paired_lines": _paired_quantity_forms(formset, rows),
                "show_expected": False,
                "progress": session_progress(session),
            },
        )

    def get(self, request, pk):
        session = _session_or_404(pk)
        try:
            _require_started(session)
        except ValidationError as exc:
            messages.error(request, _message_for_validation(exc))
            return redirect("counting:session-detail", pk=session.pk)
        formset, rows = self._formset(session)
        return self._render(request, session, formset, rows)

    def post(self, request, pk):
        session = _session_or_404(pk)
        try:
            _require_started(session)
        except ValidationError as exc:
            messages.error(request, _message_for_validation(exc))
            return redirect("counting:session-detail", pk=session.pk)
        formset, rows = self._formset(session, data=request.POST)
        if not formset.is_valid():
            return self._render(request, session, formset, rows)
        line_by_id = {line.pk: line for line in rows}
        saved = 0
        had_error = False
        for form in formset.forms:
            quantity = form.cleaned_data.get("counted_quantity")
            if quantity is None:
                continue
            line = line_by_id.get(form.cleaned_data["line_id"])
            if line is None:
                form.add_error(None, "Sayım satırı bu oturuma ait değil.")
                had_error = True
                continue
            try:
                record_quantity_count(
                    actor=request.user,
                    session_id=session.pk,
                    line_id=line.pk,
                    counted_quantity=quantity,
                    expected_counted_at=form.cleaned_data.get("counted_at_token"),
                )
            except PermissionDenied:
                raise
            except ValidationError as exc:
                _attach_validation_error(form, exc)
                had_error = True
                continue
            saved += 1
        if had_error:
            return self._render(request, session, formset, rows)
        if saved:
            messages.success(request, f"{saved} miktar satırı kaydedildi.")
        else:
            messages.info(
                request,
                "Boş bırakılan satırlar işlenmedi; bekleyen sayım olduğu gibi kaldı.",
            )
        return redirect("counting:quantity-count", pk=session.pk)


class QuantityNotCountedView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = CHANGE_COUNT_PERMISSION
    http_method_names = ["post"]

    def post(self, request, pk, line_pk):
        session = _session_or_404(pk)
        formset, rows = QuantityCountView()._formset(session, data=None)
        token_map = {
            str(line.pk): counted_at_token(line.counted_at) for line in rows
        }
        expected_counted_at = None
        raw_token = request.POST.get("counted_at_token") or token_map.get(
            str(line_pk)
        )
        if raw_token:
            from counting.forms import CountedAtTokenField

            field = CountedAtTokenField()
            try:
                expected_counted_at = field.clean(raw_token)
            except ValidationError as exc:
                messages.error(request, _message_for_validation(exc))
                return redirect("counting:quantity-count", pk=session.pk)
        try:
            mark_quantity_line_not_counted(
                actor=request.user,
                session_id=session.pk,
                line_id=line_pk,
                expected_counted_at=expected_counted_at,
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            messages.error(request, _message_for_validation(exc))
            return redirect("counting:quantity-count", pk=session.pk)
        messages.success(request, "Satır açıkça sayılmadı olarak işaretlendi.")
        return redirect("counting:quantity-count", pk=session.pk)


class UnexpectedQuantityView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = CHANGE_COUNT_PERMISSION
    template_name = "counting/unexpected_quantity_form.html"

    def _render(self, request, session, form):
        return render(
            request,
            self.template_name,
            {"session": session, "form": form, "show_expected": False},
        )

    def get(self, request, pk):
        session = _session_or_404(pk)
        try:
            _require_started(session)
        except ValidationError as exc:
            messages.error(request, _message_for_validation(exc))
            return redirect("counting:session-detail", pk=session.pk)
        form = UnexpectedQuantityForm(
            location_queryset=scope_stock_location_queryset(session)
        )
        return self._render(request, session, form)

    def post(self, request, pk):
        session = _session_or_404(pk)
        try:
            _require_started(session)
        except ValidationError as exc:
            messages.error(request, _message_for_validation(exc))
            return redirect("counting:session-detail", pk=session.pk)
        form = UnexpectedQuantityForm(
            request.POST,
            location_queryset=scope_stock_location_queryset(session),
        )
        if not form.is_valid():
            return self._render(request, session, form)
        try:
            add_unexpected_quantity_count(
                actor=request.user,
                session_id=session.pk,
                material_id=form.cleaned_data["material"].pk,
                location_id=form.cleaned_data["location"].pk,
                condition_id=form.cleaned_data["condition"].pk,
                counted_quantity=form.cleaned_data["counted_quantity"],
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self._render(request, session, form)
        messages.success(request, "Beklenmeyen miktar gözlemi kaydedildi.")
        return redirect("counting:quantity-count", pk=session.pk)


def _lookup_serialized_identity(raw_value):
    identity = (raw_value or "").strip()
    if not identity:
        return None, None, None
    try:
        decoded = decode_payload(identity)
    except IdentityCodecError:
        decoded = None
    if decoded is not None:
        if decoded.entity_type == IdentityEntityType.SERIALIZED_ASSET:
            asset = SerializedAsset.objects.filter(pk=decoded.object_id).first()
            return asset, None, None
        if decoded.entity_type == IdentityEntityType.LOCATION:
            location = Location.objects.filter(pk=decoded.object_id).first()
            return None, location, None
        if decoded.entity_type == IdentityEntityType.MATERIAL:
            material = Material.objects.filter(pk=decoded.object_id).first()
            return None, None, material
    asset = SerializedAsset.objects.filter(internal_asset_code=identity).first()
    return asset, None, None


class SerializedCountView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = CHANGE_COUNT_PERMISSION
    template_name = "counting/serialized_count.html"

    def _render(self, request, session, extra=None):
        lines = list(
            session.serialized_lines.select_related(
                "material",
                "serialized_asset",
                "observed_location",
                "observed_condition",
                "counted_by_user",
            ).order_by("internal_asset_code", "id")
        )
        location_qs = scope_stock_location_queryset(session)
        context = {
            "session": session,
            "serialized_lines": lines,
            "lookup_form": extra.get("lookup_form")
            if extra
            else SerializedIdentityLookupForm(request.GET or None),
            "observe_form": extra.get("observe_form")
            if extra
            else None,
            "resolved_asset": extra.get("resolved_asset") if extra else None,
            "resolved_location": extra.get("resolved_location") if extra else None,
            "resolved_material": extra.get("resolved_material") if extra else None,
            "lookup_error": extra.get("lookup_error") if extra else None,
            "show_expected": False,
            "progress": session_progress(session),
            "location_queryset": location_qs,
        }
        if extra:
            context.update({k: v for k, v in extra.items() if k not in context})
        if context["observe_form"] is None and context["resolved_asset"] is not None:
            asset = context["resolved_asset"]
            existing = next(
                (line for line in lines if line.serialized_asset_id == asset.pk),
                None,
            )
            context["observe_form"] = SerializedObserveForm(
                initial={
                    "serialized_asset_id": asset.pk,
                    "counted_at_token": counted_at_token(
                        existing.counted_at if existing is not None else None
                    ),
                    "observed_location": context["resolved_location"].pk
                    if context["resolved_location"] is not None
                    else (
                        existing.observed_location_id if existing is not None else None
                    ),
                },
                location_queryset=location_qs,
            )
        return render(request, self.template_name, context)

    def get(self, request, pk):
        session = _session_or_404(pk)
        try:
            _require_started(session)
        except ValidationError as extra:
            messages.error(request, _message_for_validation(extra))
            return redirect("counting:session-detail", pk=session.pk)
        lookup_form = SerializedIdentityLookupForm(request.GET or None)
        extra = {"lookup_form": lookup_form}
        identity = request.GET.get("identity", "").strip()
        if identity:
            asset, location, material = _lookup_serialized_identity(identity)
            extra["resolved_asset"] = asset
            extra["resolved_location"] = location
            extra["resolved_material"] = material
            if asset is None and location is None and material is None:
                extra["lookup_error"] = "Kimlik çözülemedi. Sayım kaydı oluşturulmadı."
        return self._render(request, session, extra)

    def post(self, request, pk):
        session = _session_or_404(pk)
        try:
            _require_started(session)
        except ValidationError as extra:
            messages.error(request, _message_for_validation(extra))
            return redirect("counting:session-detail", pk=session.pk)
        form = SerializedObserveForm(
            request.POST,
            location_queryset=scope_stock_location_queryset(session),
        )
        if not form.is_valid():
            asset = SerializedAsset.objects.filter(
                pk=request.POST.get("serialized_asset_id")
            ).first()
            return self._render(
                request,
                session,
                {
                    "observe_form": form,
                    "resolved_asset": asset,
                    "lookup_form": SerializedIdentityLookupForm(),
                },
            )
        try:
            record_serialized_asset_count(
                actor=request.user,
                session_id=session.pk,
                serialized_asset_id=form.cleaned_data["serialized_asset_id"],
                observed_location_id=form.cleaned_data["observed_location"].pk,
                observed_condition_id=form.cleaned_data["observed_condition"].pk,
                expected_counted_at=form.cleaned_data.get("counted_at_token"),
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            asset = SerializedAsset.objects.filter(
                pk=form.cleaned_data["serialized_asset_id"]
            ).first()
            return self._render(
                request,
                session,
                {
                    "observe_form": form,
                    "resolved_asset": asset,
                    "lookup_form": SerializedIdentityLookupForm(),
                },
            )
        messages.success(request, "Tekil varlık gözlemi kaydedildi.")
        return redirect("counting:serialized-count", pk=session.pk)


class SerializedMissingView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = CHANGE_COUNT_PERMISSION
    http_method_names = ["post"]

    def post(self, request, pk, line_pk):
        session = _session_or_404(pk)
        raw_token = request.POST.get("counted_at_token") or ""
        expected_counted_at = None
        if raw_token:
            from counting.forms import CountedAtTokenField

            try:
                expected_counted_at = CountedAtTokenField().clean(raw_token)
            except ValidationError as exc:
                messages.error(request, _message_for_validation(exc))
                return redirect("counting:serialized-count", pk=session.pk)
        try:
            mark_serialized_asset_missing(
                actor=request.user,
                session_id=session.pk,
                line_id=line_pk,
                expected_counted_at=expected_counted_at,
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            messages.error(request, _message_for_validation(exc))
            return redirect("counting:serialized-count", pk=session.pk)
        messages.success(request, "Beklenen tekil varlık eksik olarak işaretlendi.")
        return redirect("counting:serialized-count", pk=session.pk)


class SerializedNotCountedView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = CHANGE_COUNT_PERMISSION
    http_method_names = ["post"]

    def post(self, request, pk, line_pk):
        session = _session_or_404(pk)
        raw_token = request.POST.get("counted_at_token") or ""
        expected_counted_at = None
        if raw_token:
            from counting.forms import CountedAtTokenField

            try:
                expected_counted_at = CountedAtTokenField().clean(raw_token)
            except ValidationError as exc:
                messages.error(request, _message_for_validation(exc))
                return redirect("counting:serialized-count", pk=session.pk)
        try:
            mark_serialized_line_not_counted(
                actor=request.user,
                session_id=session.pk,
                line_id=line_pk,
                expected_counted_at=expected_counted_at,
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            messages.error(request, _message_for_validation(exc))
            return redirect("counting:serialized-count", pk=session.pk)
        messages.success(request, "Tekil satır açıkça sayılmadı olarak işaretlendi.")
        return redirect("counting:serialized-count", pk=session.pk)


class CandidateSerializedView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = CHANGE_COUNT_PERMISSION
    template_name = "counting/candidate_serialized_form.html"

    def _render(self, request, session, form):
        return render(
            request,
            self.template_name,
            {"session": session, "form": form, "show_expected": False},
        )

    def get(self, request, pk):
        session = _session_or_404(pk)
        try:
            _require_started(session)
        except ValidationError as extra:
            messages.error(request, _message_for_validation(extra))
            return redirect("counting:session-detail", pk=session.pk)
        initial = {}
        identity = request.GET.get("identity", "").strip()
        if identity:
            asset, location, material = _lookup_serialized_identity(identity)
            if asset is not None:
                messages.error(
                    request,
                    "Bu kimlik yetkili bir tekil varlığa aittir; aday satır oluşturulamaz.",
                )
                return redirect("counting:serialized-count", pk=session.pk)
            if material is not None:
                initial["material"] = material.pk
            if location is not None:
                initial["observed_location"] = location.pk
        form = CandidateSerializedForm(
            initial=initial,
            location_queryset=scope_stock_location_queryset(session),
        )
        return self._render(request, session, form)

    def post(self, request, pk):
        session = _session_or_404(pk)
        try:
            _require_started(session)
        except ValidationError as extra:
            messages.error(request, _message_for_validation(extra))
            return redirect("counting:session-detail", pk=session.pk)
        form = CandidateSerializedForm(
            request.POST,
            location_queryset=scope_stock_location_queryset(session),
        )
        if not form.is_valid():
            return self._render(request, session, form)
        try:
            add_candidate_serialized_count(
                actor=request.user,
                session_id=session.pk,
                material_id=form.cleaned_data["material"].pk,
                internal_asset_code=form.cleaned_data["internal_asset_code"],
                serial_number=form.cleaned_data.get("serial_number") or None,
                observed_location_id=form.cleaned_data["observed_location"].pk,
                observed_condition_id=form.cleaned_data["observed_condition"].pk,
            )
        except PermissionDenied:
            raise
        except ValidationError as exc:
            _attach_validation_error(form, exc)
            return self._render(request, session, form)
        messages.success(
            request,
            "Aday tekil gözlem kaydedildi. Yetkili varlık oluşturulmadı.",
        )
        return redirect("counting:serialized-count", pk=session.pk)


class PhysicalCountCompleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = CHANGE_COUNT_PERMISSION

    def get(self, request, pk):
        session = _session_or_404(pk)
        if session.status != PhysicalCountSession.Status.STARTED:
            messages.error(request, "Yalnız başlatılmış sayım oturumu tamamlanabilir.")
            return redirect("counting:session-detail", pk=session.pk)
        return render(
            request,
            "counting/session_complete_confirm.html",
            {
                "session": session,
                "progress": session_progress(session),
            },
        )

    def post(self, request, pk):
        session = _session_or_404(pk)
        try:
            complete_physical_count(actor=request.user, session_id=session.pk)
        except PermissionDenied:
            raise
        except ValidationError as extra:
            messages.error(request, _message_for_validation(extra))
            return redirect("counting:session-complete", pk=session.pk)
        messages.success(request, "Fiziksel sayım tamamlandı. Stok değişmedi.")
        return redirect("counting:session-detail", pk=session.pk)


class DiscrepancyReviewView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = DECIDE_COUNT_PERMISSION
    template_name = "counting/discrepancy_review.html"
    http_method_names = ["get", "head"]

    def get(self, request, pk):
        session = _session_or_404(pk)
        quantity_lines = list(
            session.quantity_lines.select_related(
                "material__unit",
                "location",
                "condition",
                "counted_by_user",
                "approved_by_user",
                "reconciliation_transaction",
            ).order_by("location__code", "material__material_code", "id")
        )
        serialized_lines = list(
            session.serialized_lines.select_related(
                "material",
                "serialized_asset",
                "expected_location",
                "expected_condition",
                "observed_location",
                "observed_condition",
                "counted_by_user",
            ).order_by("internal_asset_code", "id")
        )
        pending_items = []
        other_quantity_lines = []
        for line in quantity_lines:
            if (
                line.resolution_status
                == PhysicalCountQuantityLine.ResolutionStatus.PENDING_APPROVAL
                and line.counted_by_user_id != request.user.pk
            ):
                pending_items.append(
                    {"line": line, "approval_form": QuantityDiscrepancyApprovalForm()}
                )
            else:
                other_quantity_lines.append(line)
        return render(
            request,
            self.template_name,
            {
                "session": session,
                "pending_items": pending_items,
                "other_quantity_lines": other_quantity_lines,
                "quantity_lines": quantity_lines,
                "serialized_lines": serialized_lines,
                "rejection_form": QuantityDiscrepancyRejectionForm(),
                "show_expected": True,
                "can_decide": True,
            },
        )


class QuantityDiscrepancyApproveView(
    LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = DECIDE_COUNT_PERMISSION
    http_method_names = ["post"]

    def post(self, request, pk, line_pk):
        session = _session_or_404(pk)
        form = QuantityDiscrepancyApprovalForm(request.POST)
        if not form.is_valid():
            messages.error(
                request,
                "Onay açıklaması 10 ile 2000 karakter arasında olmalıdır.",
            )
            return redirect("counting:discrepancy-review", pk=session.pk)
        try:
            result = approve_quantity_discrepancy(
                actor=request.user,
                session_id=session.pk,
                line_id=line_pk,
                operation_id=form.cleaned_data["operation_id"],
                explanation=form.cleaned_data["explanation"],
            )
        except PermissionDenied:
            raise
        except ValidationError as extra:
            messages.error(request, _message_for_validation(extra))
            return redirect("counting:discrepancy-review", pk=session.pk)
        if result.replayed:
            messages.info(request, "Bu sayım farkı daha önce onaylanmıştı.")
        else:
            messages.success(request, "Sayım farkı onaylandı ve mutabakat uygulandı.")
        return redirect("counting:discrepancy-review", pk=session.pk)


class QuantityDiscrepancyRejectView(
    LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = DECIDE_COUNT_PERMISSION
    http_method_names = ["post"]

    def post(self, request, pk, line_pk):
        session = _session_or_404(pk)
        form = QuantityDiscrepancyRejectionForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Ret gerekçesi en fazla 2000 karakter olabilir.")
            return redirect("counting:discrepancy-review", pk=session.pk)
        try:
            reject_quantity_discrepancy(
                actor=request.user,
                session_id=session.pk,
                line_id=line_pk,
                reason=form.cleaned_data.get("reason") or None,
            )
        except PermissionDenied:
            raise
        except ValidationError as extra:
            messages.error(request, _message_for_validation(extra))
            return redirect("counting:discrepancy-review", pk=session.pk)
        messages.success(
            request,
            "Fark reddedildi. Oturum yeniden sayıma açıldı; stok değişmedi.",
        )
        return redirect("counting:session-detail", pk=session.pk)
