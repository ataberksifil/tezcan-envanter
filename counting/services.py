from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation, localcontext

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connections, transaction
from django.utils import timezone

from catalog.models import Material, MaterialCondition
from counting.models import (
    PhysicalCountQuantityLine,
    PhysicalCountQuantityRejection,
    PhysicalCountSerializedLine,
    PhysicalCountSession,
)
from inventory.models import InventoryTransaction, SerializedAsset, StockBalance
from inventory.services.receipts import (
    normalize_internal_asset_code,
    normalize_serial_number,
)
from inventory.services.reconciliations import (
    DECIDE_COUNT_PERMISSION,
    reconcile_quantity_count,
)
from counting.queries import (
    load_location_parent_map,
    resolve_location_subtree_ids,
    subtrees_overlap,
)
from locations.models import Location


# Serializes count-scope create/start decisions. The Location table SHARE lock used
# during the same transaction makes the in-memory tree view stable against parent
# changes without creating a locations -> counting dependency.
COUNT_SCOPE_ADVISORY_LOCK_KEY = 831_054

INVALID_ACTOR = "counting.invalid_actor"
INVALID_SESSION = "counting.invalid_session"
INVALID_SCOPE = "counting.invalid_scope"
OVERLAPPING_SCOPE = "counting.overlapping_scope"
INVALID_LINE = "counting.invalid_line"
INVALID_QUANTITY = "counting.invalid_quantity"
COUNT_CONFLICT = "counting.count_conflict"
DUPLICATE_BUCKET = "counting.duplicate_bucket"
DUPLICATE_SERIALIZED_IDENTITY = "counting.duplicate_serialized_identity"
AUTHORITATIVE_ASSET_EXISTS = "counting.authoritative_asset_exists"
INVALID_SERIALIZED_ASSET = "counting.invalid_serialized_asset"
INCOMPLETE_COUNT = "counting.incomplete_count"
INVALID_RECONCILIATION = "counting.invalid_reconciliation"
SELF_APPROVAL = "counting.self_approval"
INVALID_APPROVAL_EXPLANATION = "counting.invalid_approval_explanation"

QUANTITY_QUANTUM = Decimal("0.001")
MAX_QUANTITY = Decimal("999999999999999.999")


@dataclass(frozen=True)
class CountSessionStartResult:
    session: PhysicalCountSession
    quantity_lines: tuple[PhysicalCountQuantityLine, ...]
    serialized_lines: tuple[PhysicalCountSerializedLine, ...] = ()


@dataclass(frozen=True)
class QuantityDiscrepancyApprovalResult:
    line: PhysicalCountQuantityLine
    transaction: InventoryTransaction
    replayed: bool


def create_physical_count_session(
    *,
    actor,
    reference_number,
    scope_location_id,
    baseline_candidate: bool = False,
    using: str = "default",
) -> PhysicalCountSession:
    """Create a DRAFT session while preserving the open-scope invariant."""
    _current_actor(actor, using)
    reference = _normalize_reference_number(reference_number)
    scope_id = _normalize_uuid(scope_location_id, code=INVALID_SCOPE)

    with transaction.atomic(using=using):
        _acquire_count_scope_lock(using)
        _lock_location_tree(using)
        parent_map = load_location_parent_map(using=using)
        _validate_scope_root(scope_id, using)
        _reject_overlapping_open_session(
            scope_id=scope_id,
            parent_map=parent_map,
            excluding_session_id=None,
            using=using,
        )
        session = PhysicalCountSession(
            reference_number=reference,
            scope_location_id=scope_id,
            baseline_candidate=bool(baseline_candidate),
        )
        session._state.db = using
        session.full_clean(validate_constraints=False)
        try:
            with transaction.atomic(using=using):
                session.save(using=using, force_insert=True)
        except IntegrityError as exc:
            if _constraint_name(exc) == "counting_session_reference_uniq":
                raise ValidationError(
                    "Bu sayım referansı zaten kullanılıyor.",
                    code=INVALID_SESSION,
                ) from exc
            raise
        return session


def start_physical_count_session(
    *,
    actor,
    session_id,
    using: str = "default",
) -> CountSessionStartResult:
    """Atomically capture QUANTITY and SERIALIZED snapshots and DRAFT -> STARTED.

    Counting lock order is session -> Material -> Location -> MaterialCondition ->
    StockBalance -> SerializedAsset. All materials are locked in primary-key order so
    serialized RECEIVE, which takes Material before inserting SerializedAsset, cannot
    invert this total order. The short SHARE lock on serialized_assets is taken after
    those masters; it only serializes concurrent asset inserts against this snapshot
    boundary. Locks are transaction-scoped; inventory remains unfrozen after commit.
    """
    current_actor = _current_actor(actor, using)
    normalized_session_id = _normalize_uuid(session_id, code=INVALID_SESSION)

    with transaction.atomic(using=using):
        session = _locked_session(normalized_session_id, using)
        if session.status != PhysicalCountSession.Status.DRAFT:
            raise ValidationError(
                "Yalnız taslak sayım oturumu başlatılabilir.",
                code=INVALID_SESSION,
            )
        if (
            session.quantity_lines.using(using).exists()
            or session.serialized_lines.using(using).exists()
        ):
            raise ValidationError(
                "Taslak oturumda başlangıç öncesi sayım satırı bulunamaz.",
                code=INVALID_SESSION,
            )

        _acquire_count_scope_lock(using)

        # An unlocked first pass avoids taking global master-data row locks when an
        # already-known overlap can be rejected immediately. The check is repeated
        # after the Location tree has been stabilized.
        initial_parent_map = load_location_parent_map(using=using)
        _reject_overlapping_open_session(
            scope_id=session.scope_location_id,
            parent_map=initial_parent_map,
            excluding_session_id=session.pk,
            using=using,
        )

        _lock_all_materials(using)
        _lock_location_tree(using)
        parent_map = load_location_parent_map(using=using)
        subtree_ids = resolve_location_subtree_ids(
            session.scope_location_id,
            parent_map=parent_map,
            using=using,
        )
        locked_locations = _lock_locations(subtree_ids, using)
        _validate_locked_scope_root(session.scope_location_id, locked_locations)
        _reject_overlapping_open_session(
            scope_id=session.scope_location_id,
            parent_map=parent_map,
            excluding_session_id=session.pk,
            using=using,
        )
        _lock_all_conditions(using)

        balances = tuple(
            StockBalance.objects.using(using)
            .select_for_update()
            .filter(
                location_id__in=subtree_ids,
                quantity__gt=Decimal("0"),
                material__tracking_mode=Material.TrackingMode.QUANTITY,
            )
            .order_by("material_id", "location_id", "condition_id", "pk")
        )
        lines = tuple(
            PhysicalCountQuantityLine(
                session=session,
                material_id=balance.material_id,
                location_id=balance.location_id,
                condition_id=balance.condition_id,
                expected_quantity=balance.quantity,
            )
            for balance in balances
        )
        if lines:
            PhysicalCountQuantityLine.objects.using(using).bulk_create(lines)

        # SHARE is compatible with SELECT FOR UPDATE on existing assets and conflicts
        # with INSERT. Serialized RECEIVE inserts only after Material/Location/
        # Condition; those masters are already locked above, so this does not invert
        # identifier uniqueness ahead of masters.
        _lock_serialized_asset_table(using)
        assets = tuple(
            SerializedAsset.objects.using(using)
            .select_for_update()
            .filter(
                current_state=SerializedAsset.CurrentState.IN_STOCK,
                current_location_id__in=subtree_ids,
                material__tracking_mode=Material.TrackingMode.SERIALIZED,
            )
            .order_by("pk")
        )
        serialized_lines = tuple(
            PhysicalCountSerializedLine(
                session=session,
                serialized_asset_id=asset.pk,
                material_id=asset.material_id,
                internal_asset_code=asset.internal_asset_code,
                serial_number=asset.serial_number,
                expected_present=True,
                expected_location_id=asset.current_location_id,
                expected_condition_id=asset.current_condition_id,
            )
            for asset in assets
        )
        if serialized_lines:
            PhysicalCountSerializedLine.objects.using(using).bulk_create(
                serialized_lines
            )

        started_at = timezone.now()
        session.status = PhysicalCountSession.Status.STARTED
        session.started_by_user = current_actor
        session.started_at = started_at
        session.save(
            using=using,
            update_fields=["status", "started_by_user", "started_at", "updated_at"],
        )
        return CountSessionStartResult(
            session=session,
            quantity_lines=lines,
            serialized_lines=serialized_lines,
        )


def record_quantity_count(
    *,
    actor,
    session_id,
    line_id,
    counted_quantity,
    expected_counted_at=None,
    using: str = "default",
) -> PhysicalCountQuantityLine:
    """Record or explicitly re-count one line using counted_at as a CAS token."""
    current_actor = _current_actor(actor, using)
    normalized_session_id = _normalize_uuid(session_id, code=INVALID_SESSION)
    normalized_line_id = _normalize_uuid(line_id, code=INVALID_LINE)
    quantity = _normalize_nonnegative_quantity(counted_quantity)

    with transaction.atomic(using=using):
        session = _locked_session(normalized_session_id, using)
        _require_started(session)
        line = _locked_line(normalized_line_id, normalized_session_id, using)
        _lock_material(line.material_id, using)
        _lock_location(line.location_id, using)
        _lock_condition(line.condition_id, using)

        if line.resolution_status in {
            PhysicalCountQuantityLine.ResolutionStatus.APPROVED,
            PhysicalCountQuantityLine.ResolutionStatus.DISPOSITIONED,
        }:
            raise ValidationError(
                "Sonuçlandırılmış sayım satırı yeniden sayılamaz.",
                code=INVALID_LINE,
            )
        if line.counted_at != expected_counted_at:
            raise ValidationError(
                "Sayım satırı başka bir kullanıcı tarafından güncellendi.",
                code=COUNT_CONFLICT,
            )

        counted_at = timezone.now()
        if line.counted_at is not None and counted_at <= line.counted_at:
            counted_at = line.counted_at + timedelta(microseconds=1)
        line.counted_quantity = quantity
        line.counted_by_user = current_actor
        line.counted_at = counted_at
        line.resolution_status = (
            PhysicalCountQuantityLine.ResolutionStatus.NO_DISCREPANCY
            if quantity == line.expected_quantity
            else PhysicalCountQuantityLine.ResolutionStatus.PENDING_APPROVAL
        )
        line.save(
            using=using,
            update_fields=[
                "counted_quantity",
                "counted_by_user",
                "counted_at",
                "resolution_status",
            ],
        )
        return line


def mark_quantity_line_not_counted(
    *,
    actor,
    session_id,
    line_id,
    expected_counted_at=None,
    using: str = "default",
) -> PhysicalCountQuantityLine:
    """Persist an explicit not-counted action without inventing a zero count."""
    current_actor = _current_actor(actor, using)
    normalized_session_id = _normalize_uuid(session_id, code=INVALID_SESSION)
    normalized_line_id = _normalize_uuid(line_id, code=INVALID_LINE)

    with transaction.atomic(using=using):
        session = _locked_session(normalized_session_id, using)
        _require_started(session)
        line = _locked_line(normalized_line_id, normalized_session_id, using)
        _lock_material(line.material_id, using)
        _lock_location(line.location_id, using)
        _lock_condition(line.condition_id, using)

        if line.expected_quantity == 0:
            raise ValidationError(
                "Beklenmeyen stok satırı sayılmadı olarak işaretlenemez.",
                code=INVALID_LINE,
            )
        if line.resolution_status in {
            PhysicalCountQuantityLine.ResolutionStatus.APPROVED,
            PhysicalCountQuantityLine.ResolutionStatus.DISPOSITIONED,
        }:
            raise ValidationError(
                "Sonuçlandırılmış sayım satırı değiştirilemez.",
                code=INVALID_LINE,
            )
        if line.counted_at != expected_counted_at:
            raise ValidationError(
                "Sayım satırı başka bir kullanıcı tarafından güncellendi.",
                code=COUNT_CONFLICT,
            )

        counted_at = timezone.now()
        if line.counted_at is not None and counted_at <= line.counted_at:
            counted_at = line.counted_at + timedelta(microseconds=1)
        line.counted_quantity = None
        line.counted_by_user = current_actor
        line.counted_at = counted_at
        line.resolution_status = (
            PhysicalCountQuantityLine.ResolutionStatus.NOT_COUNTED
        )
        line.save(
            using=using,
            update_fields=[
                "counted_quantity",
                "counted_by_user",
                "counted_at",
                "resolution_status",
            ],
        )
        return line


def add_unexpected_quantity_count(
    *,
    actor,
    session_id,
    material_id,
    location_id,
    condition_id,
    counted_quantity,
    using: str = "default",
) -> PhysicalCountQuantityLine:
    """Add explicitly found known stock that was absent from the start snapshot."""
    current_actor = _current_actor(actor, using)
    normalized_session_id = _normalize_uuid(session_id, code=INVALID_SESSION)
    normalized_material_id = _normalize_uuid(material_id, code=INVALID_LINE)
    normalized_location_id = _normalize_uuid(location_id, code=INVALID_SCOPE)
    normalized_condition_id = _normalize_uuid(condition_id, code=INVALID_LINE)
    quantity = _normalize_nonnegative_quantity(counted_quantity)

    with transaction.atomic(using=using):
        session = _locked_session(normalized_session_id, using)
        _require_started(session)
        _acquire_count_scope_lock(using)
        material = _lock_material(normalized_material_id, using)
        _lock_location_tree(using)
        location = _lock_location(normalized_location_id, using)
        condition = _lock_condition(normalized_condition_id, using)

        subtree_ids = resolve_location_subtree_ids(
            session.scope_location_id,
            parent_map=load_location_parent_map(using=using),
            using=using,
        )
        if location.pk not in subtree_ids:
            raise ValidationError(
                "Bulunan stok lokasyonu sayım kapsamı dışında.",
                code=INVALID_SCOPE,
            )
        if material.tracking_mode != Material.TrackingMode.QUANTITY:
            raise ValidationError(
                "Beklenmeyen miktar satırı için QUANTITY malzeme zorunludur.",
                code=INVALID_LINE,
            )
        if not material.active:
            raise ValidationError("Malzeme aktif olmalıdır.", code=INVALID_LINE)
        if not location.active or not location.can_hold_stock:
            raise ValidationError(
                "Sayım lokasyonu aktif ve stok tutabilir olmalıdır.",
                code=INVALID_SCOPE,
            )
        if not condition.active:
            raise ValidationError("Malzeme kondisyonu aktif olmalıdır.", code=INVALID_LINE)

        identity = {
            "session_id": session.pk,
            "material_id": material.pk,
            "location_id": location.pk,
            "condition_id": condition.pk,
        }
        if (
            PhysicalCountQuantityLine.objects.using(using)
            .select_for_update()
            .filter(**identity)
            .exists()
        ):
            raise ValidationError(
                "Bu sayım bucket'ı oturumda zaten bulunuyor.",
                code=DUPLICATE_BUCKET,
            )

        line = PhysicalCountQuantityLine(
            **identity,
            expected_quantity=Decimal("0.000"),
            counted_quantity=quantity,
            counted_by_user=current_actor,
            counted_at=timezone.now(),
            resolution_status=(
                PhysicalCountQuantityLine.ResolutionStatus.NO_DISCREPANCY
                if quantity == 0
                else PhysicalCountQuantityLine.ResolutionStatus.PENDING_APPROVAL
            ),
        )
        try:
            with transaction.atomic(using=using):
                line.save(using=using, force_insert=True)
        except IntegrityError as exc:
            if _constraint_name(exc) == "counting_qline_bucket_uniq":
                raise ValidationError(
                    "Bu sayım bucket'ı oturumda zaten bulunuyor.",
                    code=DUPLICATE_BUCKET,
                ) from exc
            raise
        return line


def record_serialized_asset_count(
    *,
    actor,
    session_id,
    serialized_asset_id,
    observed_location_id,
    observed_condition_id,
    expected_counted_at=None,
    using: str = "default",
) -> PhysicalCountSerializedLine:
    """Record a physical observation of an existing authoritative serialized asset."""
    current_actor = _current_actor(actor, using)
    normalized_session_id = _normalize_uuid(session_id, code=INVALID_SESSION)
    normalized_asset_id = _normalize_uuid(
        serialized_asset_id, code=INVALID_SERIALIZED_ASSET
    )
    normalized_location_id = _normalize_uuid(observed_location_id, code=INVALID_SCOPE)
    normalized_condition_id = _normalize_uuid(
        observed_condition_id, code=INVALID_LINE
    )

    with transaction.atomic(using=using):
        session = _locked_session(normalized_session_id, using)
        _require_started(session)
        line = (
            PhysicalCountSerializedLine.objects.using(using)
            .select_for_update()
            .filter(session_id=session.pk, serialized_asset_id=normalized_asset_id)
            .first()
        )
        if line is None:
            _acquire_count_scope_lock(using)
        asset_ref = _serialized_asset_ref(normalized_asset_id, using)
        material = _lock_material(asset_ref.material_id, using)
        _lock_location_tree(using)
        location = _lock_location(normalized_location_id, using)
        condition = _lock_condition(normalized_condition_id, using)
        asset = _locked_serialized_asset(normalized_asset_id, using)
        _require_serialized_observation_masters(
            session=session,
            material=material,
            location=location,
            condition=condition,
            using=using,
        )
        if asset.material_id != material.pk:
            raise ValidationError(
                "Tekil varlık malzeme kimliği değişemez.",
                code=INVALID_SERIALIZED_ASSET,
            )

        if line is None:
            line = _create_unexpected_serialized_line(
                session=session,
                actor=current_actor,
                asset=asset,
                location=location,
                condition=condition,
                using=using,
            )
            return line

        _apply_serialized_cas(line, expected_counted_at)
        line.observed_present = True
        line.observed_location = location
        line.observed_condition = condition
        line.counted_by_user = current_actor
        line.counted_at = _next_counted_at(line.counted_at)
        line.resolution_status = _serialized_presence_resolution(line)
        line.save(
            using=using,
            update_fields=[
                "observed_present",
                "observed_location",
                "observed_condition",
                "counted_by_user",
                "counted_at",
                "resolution_status",
            ],
        )
        return line


def mark_serialized_asset_missing(
    *,
    actor,
    session_id,
    line_id,
    expected_counted_at=None,
    using: str = "default",
) -> PhysicalCountSerializedLine:
    """Persist an explicit missing observation for an expected serialized asset."""
    current_actor = _current_actor(actor, using)
    normalized_session_id = _normalize_uuid(session_id, code=INVALID_SESSION)
    normalized_line_id = _normalize_uuid(line_id, code=INVALID_LINE)

    with transaction.atomic(using=using):
        session = _locked_session(normalized_session_id, using)
        _require_started(session)
        line = _locked_serialized_line(
            normalized_line_id, normalized_session_id, using
        )
        if not line.expected_present or line.serialized_asset_id is None:
            raise ValidationError(
                "Yalnız beklenen tekil varlık eksik olarak işaretlenebilir.",
                code=INVALID_LINE,
            )
        _lock_material(line.material_id, using)
        if line.expected_location_id is not None:
            _lock_location(line.expected_location_id, using)
        if line.expected_condition_id is not None:
            _lock_condition(line.expected_condition_id, using)
        _locked_serialized_asset(line.serialized_asset_id, using)
        _apply_serialized_cas(line, expected_counted_at)

        line.observed_present = False
        line.observed_location = None
        line.observed_condition = None
        line.counted_by_user = current_actor
        line.counted_at = _next_counted_at(line.counted_at)
        line.resolution_status = (
            PhysicalCountSerializedLine.ResolutionStatus.PENDING_APPROVAL
        )
        line.save(
            using=using,
            update_fields=[
                "observed_present",
                "observed_location",
                "observed_condition",
                "counted_by_user",
                "counted_at",
                "resolution_status",
            ],
        )
        return line


def mark_serialized_line_not_counted(
    *,
    actor,
    session_id,
    line_id,
    expected_counted_at=None,
    using: str = "default",
) -> PhysicalCountSerializedLine:
    """Persist an explicit not-counted action without inventing a missing result."""
    current_actor = _current_actor(actor, using)
    normalized_session_id = _normalize_uuid(session_id, code=INVALID_SESSION)
    normalized_line_id = _normalize_uuid(line_id, code=INVALID_LINE)

    with transaction.atomic(using=using):
        session = _locked_session(normalized_session_id, using)
        _require_started(session)
        line = _locked_serialized_line(
            normalized_line_id, normalized_session_id, using
        )
        _lock_material(line.material_id, using)
        if line.expected_location_id is not None:
            _lock_location(line.expected_location_id, using)
        if line.expected_condition_id is not None:
            _lock_condition(line.expected_condition_id, using)
        if line.serialized_asset_id is not None:
            _locked_serialized_asset(line.serialized_asset_id, using)

        if not line.expected_present or line.serialized_asset_id is None:
            raise ValidationError(
                "Beklenmeyen tekil satır sayılmadı olarak işaretlenemez.",
                code=INVALID_LINE,
            )
        _apply_serialized_cas(line, expected_counted_at)

        line.observed_present = None
        line.observed_location = None
        line.observed_condition = None
        line.counted_by_user = current_actor
        line.counted_at = _next_counted_at(line.counted_at)
        line.resolution_status = (
            PhysicalCountSerializedLine.ResolutionStatus.NOT_COUNTED
        )
        line.save(
            using=using,
            update_fields=[
                "observed_present",
                "observed_location",
                "observed_condition",
                "counted_by_user",
                "counted_at",
                "resolution_status",
            ],
        )
        return line


def add_candidate_serialized_count(
    *,
    actor,
    session_id,
    material_id,
    internal_asset_code,
    serial_number,
    observed_location_id,
    observed_condition_id,
    using: str = "default",
) -> PhysicalCountSerializedLine:
    """Record a physically found serialized item that is not yet authoritative."""
    current_actor = _current_actor(actor, using)
    normalized_session_id = _normalize_uuid(session_id, code=INVALID_SESSION)
    normalized_material_id = _normalize_uuid(material_id, code=INVALID_LINE)
    normalized_location_id = _normalize_uuid(observed_location_id, code=INVALID_SCOPE)
    normalized_condition_id = _normalize_uuid(
        observed_condition_id, code=INVALID_LINE
    )
    normalized_code = normalize_internal_asset_code(internal_asset_code)
    normalized_serial = normalize_serial_number(serial_number)

    with transaction.atomic(using=using):
        session = _locked_session(normalized_session_id, using)
        _require_started(session)
        _acquire_count_scope_lock(using)
        material = _lock_material(normalized_material_id, using)
        _lock_location_tree(using)
        location = _lock_location(normalized_location_id, using)
        condition = _lock_condition(normalized_condition_id, using)
        _require_serialized_observation_masters(
            session=session,
            material=material,
            location=location,
            condition=condition,
            using=using,
        )
        if material.tracking_mode != Material.TrackingMode.SERIALIZED:
            raise ValidationError(
                "Aday tekil satır için SERIALIZED malzeme zorunludur.",
                code=INVALID_LINE,
            )
        if not material.active:
            raise ValidationError("Malzeme aktif olmalıdır.", code=INVALID_LINE)

        # SHARE after masters keeps this lookup ordered with serialized RECEIVE,
        # which inserts SerializedAsset only after Material/Location/Condition.
        _lock_serialized_asset_table(using)
        if (
            SerializedAsset.objects.using(using)
            .filter(internal_asset_code=normalized_code)
            .exists()
        ):
            raise ValidationError(
                "Bu dahili varlık kodu yetkili bir tekil varlığa aittir; aday satır oluşturulamaz.",
                code=AUTHORITATIVE_ASSET_EXISTS,
            )

        line = PhysicalCountSerializedLine(
            session=session,
            serialized_asset=None,
            material=material,
            internal_asset_code=normalized_code,
            serial_number=normalized_serial,
            expected_present=False,
            expected_location=None,
            expected_condition=None,
            observed_present=True,
            observed_location=location,
            observed_condition=condition,
            counted_by_user=current_actor,
            counted_at=timezone.now(),
            resolution_status=(
                PhysicalCountSerializedLine.ResolutionStatus.PENDING_APPROVAL
            ),
        )
        return _save_serialized_line(line, using)


def complete_physical_count(
    *, actor, session_id, using: str = "default"
) -> PhysicalCountSession:
    """Complete physical entry only; reconciliation state and stock stay untouched."""
    current_actor = _current_actor(actor, using)
    normalized_session_id = _normalize_uuid(session_id, code=INVALID_SESSION)

    with transaction.atomic(using=using):
        session = _locked_session(normalized_session_id, using)
        _require_started(session)
        blocking_statuses = [
            PhysicalCountQuantityLine.ResolutionStatus.PENDING_COUNT
        ]
        if session.baseline_candidate:
            blocking_statuses.append(
                PhysicalCountQuantityLine.ResolutionStatus.NOT_COUNTED
            )
        if _has_blocking_count_lines(session, blocking_statuses, using):
            message = (
                "Baseline adayı sayımda sayılmamış zorunlu satır kalamaz."
                if session.baseline_candidate
                else "Sayım tamamlanmadan önce tüm zorunlu satırlar sayılmalıdır."
            )
            raise ValidationError(message, code=INCOMPLETE_COUNT)

        completed_at = timezone.now()
        session.status = PhysicalCountSession.Status.COMPLETED
        session.reconciliation_status = (
            PhysicalCountSession.ReconciliationStatus.PENDING
            if session.baseline_candidate or _has_pending_discrepancy(session, using)
            else PhysicalCountSession.ReconciliationStatus.COMPLETED
        )
        session.completed_by_user = current_actor
        session.completed_at = completed_at
        session.save(
            using=using,
            update_fields=[
                "status",
                "reconciliation_status",
                "completed_by_user",
                "completed_at",
                "updated_at",
            ],
        )
        return session


def approve_quantity_discrepancy(
    *,
    actor,
    session_id,
    line_id,
    operation_id,
    explanation,
    using: str = "default",
) -> QuantityDiscrepancyApprovalResult:
    """Approve and atomically apply one routine QUANTITY discrepancy."""
    current_actor = _decision_actor(actor, using)
    normalized_session_id = _normalize_uuid(session_id, code=INVALID_SESSION)
    normalized_line_id = _normalize_uuid(line_id, code=INVALID_LINE)
    normalized_operation_id = _normalize_uuid(
        operation_id, code="inventory.invalid_operation_id"
    )
    normalized_explanation = _normalize_approval_explanation(explanation)

    with transaction.atomic(using=using):
        session = _locked_session(normalized_session_id, using)
        line = _locked_line(normalized_line_id, normalized_session_id, using)

        if line.resolution_status == PhysicalCountQuantityLine.ResolutionStatus.APPROVED:
            result_tx = line.reconciliation_transaction
            if result_tx.operation_id != normalized_operation_id:
                raise ValidationError(
                    "Sayım farkı daha önce başka bir işlemle onaylandı.",
                    code=INVALID_RECONCILIATION,
                )
            if line.approved_by_user_id != current_actor.pk:
                raise ValidationError(
                    "Aynı operation_id farklı onay aktörüyle tekrar kullanılamaz.",
                    code="inventory.operation_conflict",
                )
            return QuantityDiscrepancyApprovalResult(
                line=line,
                transaction=result_tx,
                replayed=True,
            )

        _require_reconciliation_eligible(session=session, line=line)
        if line.counted_by_user_id == current_actor.pk:
            raise ValidationError(
                "Sayımı yapan kullanıcı kendi farkını onaylayamaz.",
                code=SELF_APPROVAL,
            )

        # Total order: session -> count line -> operation-id reservation ->
        # Material -> Location -> MaterialCondition -> StockBalance.
        mutation = reconcile_quantity_count(
            actor=current_actor,
            operation_id=normalized_operation_id,
            count_session_id=session.pk,
            count_line_id=line.pk,
            material_id=line.material_id,
            location_id=line.location_id,
            condition_id=line.condition_id,
            expected_quantity=line.expected_quantity,
            counted_quantity=line.counted_quantity,
            using=using,
        )

        line.resolution_status = PhysicalCountQuantityLine.ResolutionStatus.APPROVED
        line.approved_by_user = current_actor
        line.approved_at = timezone.now()
        line.approval_explanation = normalized_explanation
        line.reconciliation_transaction = mutation.transaction
        line.full_clean(validate_constraints=False)
        line.save(
            using=using,
            update_fields=[
                "resolution_status",
                "approved_by_user",
                "approved_at",
                "approval_explanation",
                "reconciliation_transaction",
            ],
        )

        if not _has_pending_discrepancy(session, using):
            session.reconciliation_status = PhysicalCountSession.ReconciliationStatus.COMPLETED
            session.save(
                using=using,
                update_fields=["reconciliation_status", "updated_at"],
            )
        return QuantityDiscrepancyApprovalResult(
            line=line,
            transaction=mutation.transaction,
            replayed=mutation.replayed,
        )


def reject_quantity_discrepancy(
    *, actor, session_id, line_id, reason=None, using: str = "default"
) -> PhysicalCountQuantityLine:
    """Reject without stock effect and reopen the session for an explicit recount."""
    current_actor = _decision_actor(actor, using)
    normalized_session_id = _normalize_uuid(session_id, code=INVALID_SESSION)
    normalized_line_id = _normalize_uuid(line_id, code=INVALID_LINE)
    normalized_reason = _normalize_optional_reason(reason)

    with transaction.atomic(using=using):
        session = _locked_session(normalized_session_id, using)
        line = _locked_line(normalized_line_id, normalized_session_id, using)
        _require_reconciliation_eligible(session=session, line=line)
        PhysicalCountQuantityRejection.objects.using(using).create(
            line=line,
            rejected_by_user=current_actor,
            rejected_at=timezone.now(),
            reason=normalized_reason,
            counted_quantity=line.counted_quantity,
            counted_by_user_id=line.counted_by_user_id,
            counted_at=line.counted_at,
        )
        session.status = PhysicalCountSession.Status.STARTED
        session.reconciliation_status = PhysicalCountSession.ReconciliationStatus.NOT_STARTED
        session.completed_by_user = None
        session.completed_at = None
        session.save(
            using=using,
            update_fields=[
                "status",
                "reconciliation_status",
                "completed_by_user",
                "completed_at",
                "updated_at",
            ],
        )
        return line


def _decision_actor(actor, using):
    current_actor = _current_actor(actor, using)
    if not current_actor.has_perm(DECIDE_COUNT_PERMISSION):
        raise PermissionDenied
    return current_actor


def _require_reconciliation_eligible(*, session, line):
    if session.status != PhysicalCountSession.Status.COMPLETED:
        raise ValidationError(
            "Mutabakat yalnız tamamlanmış sayım oturumunda yapılabilir.",
            code=INVALID_RECONCILIATION,
        )
    if session.baseline_candidate:
        raise ValidationError(
            "Baseline adayı oturum COUNT_RECONCILIATION oluşturamaz.",
            code=INVALID_RECONCILIATION,
        )
    if session.reconciliation_status != PhysicalCountSession.ReconciliationStatus.PENDING:
        raise ValidationError("Mutabakat bekleyen oturum zorunludur.", code=INVALID_RECONCILIATION)
    if (
        line.resolution_status
        != PhysicalCountQuantityLine.ResolutionStatus.PENDING_APPROVAL
        or line.counted_quantity is None
        or line.counted_quantity == line.expected_quantity
    ):
        raise ValidationError("Satır onaylanabilir bir fark içermiyor.", code=INVALID_RECONCILIATION)


def _normalize_approval_explanation(value):
    if not isinstance(value, str):
        raise ValidationError("Onay açıklaması zorunludur.", code=INVALID_APPROVAL_EXPLANATION)
    normalized = value.strip()
    if not 10 <= len(normalized) <= 2000:
        raise ValidationError(
            "Onay açıklaması 10 ile 2000 karakter arasında olmalıdır.",
            code=INVALID_APPROVAL_EXPLANATION,
        )
    return normalized


def _normalize_optional_reason(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("Ret nedeni metin olmalıdır.", code=INVALID_RECONCILIATION)
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 2000:
        raise ValidationError("Ret nedeni 2000 karakteri aşamaz.", code=INVALID_RECONCILIATION)
    return normalized


def _current_actor(actor, using: str):
    # Count-entry actors stay permission-light; Phase 5.4E still owns role rollout.
    # Decision paths use `_decision_actor`, which requires counting.decide_discrepancy.
    if actor is None or getattr(actor, "pk", None) is None or actor._state.db != using:
        raise PermissionDenied
    user_model = get_user_model()
    try:
        current_actor = user_model.objects.using(using).get(pk=actor.pk)
    except user_model.DoesNotExist as exc:
        raise PermissionDenied from exc
    if not current_actor.is_active:
        raise PermissionDenied
    return current_actor


def _normalize_reference_number(value) -> str:
    if not isinstance(value, str):
        raise ValidationError("Sayım referansı zorunludur.", code=INVALID_SESSION)
    normalized = value.strip()
    if not normalized or len(normalized) > 64:
        raise ValidationError(
            "Sayım referansı 1 ile 64 karakter arasında olmalıdır.",
            code=INVALID_SESSION,
        )
    return normalized


def _normalize_uuid(value, *, code: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Geçersiz kimlik.", code=code) from exc


def _normalize_nonnegative_quantity(value) -> Decimal:
    if isinstance(value, float) or value is None:
        raise ValidationError(
            "Miktar geçerli bir Decimal değer olmalıdır.", code=INVALID_QUANTITY
        )
    try:
        decimal_value = Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(
            "Miktar geçerli bir Decimal değer olmalıdır.", code=INVALID_QUANTITY
        ) from None
    if not decimal_value.is_finite() or decimal_value < 0:
        raise ValidationError("Miktar negatif olamaz.", code=INVALID_QUANTITY)
    try:
        with localcontext() as context:
            context.prec = max(28, len(decimal_value.as_tuple().digits) + 4)
            quantized = decimal_value.quantize(QUANTITY_QUANTUM)
    except InvalidOperation:
        raise ValidationError(
            "Miktar NUMERIC(18,3) sınırını aşamaz.", code=INVALID_QUANTITY
        ) from None
    if quantized != decimal_value:
        raise ValidationError(
            "Miktar en fazla üç ondalık basamak içerebilir.", code=INVALID_QUANTITY
        )
    if quantized > MAX_QUANTITY:
        raise ValidationError(
            "Miktar NUMERIC(18,3) sınırını aşamaz.", code=INVALID_QUANTITY
        )
    return quantized


def _locked_session(session_id: uuid.UUID, using: str) -> PhysicalCountSession:
    try:
        return (
            PhysicalCountSession.objects.using(using)
            .select_for_update()
            .get(pk=session_id)
        )
    except PhysicalCountSession.DoesNotExist as exc:
        raise ValidationError("Sayım oturumu bulunamadı.", code=INVALID_SESSION) from exc


def _require_started(session: PhysicalCountSession) -> None:
    if session.status != PhysicalCountSession.Status.STARTED:
        raise ValidationError(
            "Sayım girişi yalnız STARTED oturumda yapılabilir.",
            code=INVALID_SESSION,
        )


def _locked_line(line_id, session_id, using) -> PhysicalCountQuantityLine:
    try:
        return (
            PhysicalCountQuantityLine.objects.using(using)
            .select_for_update()
            .get(pk=line_id, session_id=session_id)
        )
    except PhysicalCountQuantityLine.DoesNotExist as exc:
        raise ValidationError("Sayım satırı bulunamadı.", code=INVALID_LINE) from exc


def _lock_material(material_id, using) -> Material:
    try:
        return Material.objects.using(using).select_for_update().get(pk=material_id)
    except Material.DoesNotExist as exc:
        raise ValidationError("Malzeme bulunamadı.", code=INVALID_LINE) from exc


def _lock_location(location_id, using) -> Location:
    try:
        return Location.objects.using(using).select_for_update().get(pk=location_id)
    except Location.DoesNotExist as exc:
        raise ValidationError("Lokasyon bulunamadı.", code=INVALID_SCOPE) from exc


def _lock_condition(condition_id, using) -> MaterialCondition:
    try:
        return (
            MaterialCondition.objects.using(using)
            .select_for_update()
            .get(pk=condition_id)
        )
    except MaterialCondition.DoesNotExist as exc:
        raise ValidationError("Malzeme kondisyonu bulunamadı.", code=INVALID_LINE) from exc


def _lock_all_materials(using: str) -> None:
    list(
        Material.objects.using(using)
        .select_for_update()
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def _lock_all_conditions(using: str) -> None:
    list(
        MaterialCondition.objects.using(using)
        .select_for_update()
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def _lock_locations(location_ids, using: str) -> tuple[Location, ...]:
    return tuple(
        Location.objects.using(using)
        .select_for_update()
        .filter(pk__in=location_ids)
        .order_by("pk")
    )


def _validate_scope_root(scope_id, using: str) -> None:
    try:
        scope = Location.objects.using(using).get(pk=scope_id)
    except Location.DoesNotExist as exc:
        raise ValidationError("Sayım kapsamı bulunamadı.", code=INVALID_SCOPE) from exc
    if not scope.active:
        raise ValidationError("Sayım kapsamı aktif olmalıdır.", code=INVALID_SCOPE)


def _validate_locked_scope_root(scope_id, locations: tuple[Location, ...]) -> None:
    root = next((location for location in locations if location.pk == scope_id), None)
    if root is None or not root.active:
        raise ValidationError("Sayım kapsamı aktif olmalıdır.", code=INVALID_SCOPE)


def _reject_overlapping_open_session(
    *,
    scope_id,
    parent_map,
    excluding_session_id,
    using,
) -> None:
    sessions = PhysicalCountSession.objects.using(using).exclude(
        status=PhysicalCountSession.Status.COMPLETED
    )
    if excluding_session_id is not None:
        sessions = sessions.exclude(pk=excluding_session_id)
    for other_root_id in sessions.order_by("pk").values_list(
        "scope_location_id", flat=True
    ):
        if subtrees_overlap(scope_id, other_root_id, parent_map=parent_map):
            raise ValidationError(
                "Aynı veya çakışan lokasyon kapsamında açık sayım oturumu var.",
                code=OVERLAPPING_SCOPE,
            )


def _acquire_count_scope_lock(using: str) -> None:
    with connections[using].cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [COUNT_SCOPE_ADVISORY_LOCK_KEY])


def _lock_location_tree(using: str) -> None:
    with connections[using].cursor() as cursor:
        cursor.execute("LOCK TABLE locations_location IN SHARE MODE")


def _lock_serialized_asset_table(using: str) -> None:
    with connections[using].cursor() as cursor:
        cursor.execute("LOCK TABLE inventory_serializedasset IN SHARE MODE")


def _has_blocking_count_lines(session, blocking_statuses, using) -> bool:
    return (
        session.quantity_lines.using(using)
        .filter(resolution_status__in=blocking_statuses)
        .exists()
        or session.serialized_lines.using(using)
        .filter(resolution_status__in=blocking_statuses)
        .exists()
    )


def _has_pending_discrepancy(session, using) -> bool:
    return (
        session.quantity_lines.using(using)
        .filter(
            resolution_status=PhysicalCountQuantityLine.ResolutionStatus.PENDING_APPROVAL
        )
        .exists()
        or session.serialized_lines.using(using)
        .filter(
            resolution_status=PhysicalCountSerializedLine.ResolutionStatus.PENDING_APPROVAL
        )
        .exists()
    )


def _locked_serialized_line(line_id, session_id, using) -> PhysicalCountSerializedLine:
    try:
        return (
            PhysicalCountSerializedLine.objects.using(using)
            .select_for_update()
            .get(pk=line_id, session_id=session_id)
        )
    except PhysicalCountSerializedLine.DoesNotExist as exc:
        raise ValidationError("Sayım satırı bulunamadı.", code=INVALID_LINE) from exc


def _serialized_asset_ref(asset_id, using) -> SerializedAsset:
    try:
        return SerializedAsset.objects.using(using).get(pk=asset_id)
    except SerializedAsset.DoesNotExist as exc:
        raise ValidationError(
            "Tekil varlık bulunamadı.",
            code=INVALID_SERIALIZED_ASSET,
        ) from exc


def _locked_serialized_asset(asset_id, using) -> SerializedAsset:
    try:
        return (
            SerializedAsset.objects.using(using)
            .select_for_update()
            .get(pk=asset_id)
        )
    except SerializedAsset.DoesNotExist as exc:
        raise ValidationError(
            "Tekil varlık bulunamadı.",
            code=INVALID_SERIALIZED_ASSET,
        ) from exc


def _require_serialized_observation_masters(
    *, session, material, location, condition, using
) -> None:
    subtree_ids = resolve_location_subtree_ids(
        session.scope_location_id,
        parent_map=load_location_parent_map(using=using),
        using=using,
    )
    if location.pk not in subtree_ids:
        raise ValidationError(
            "Gözlenen lokasyon sayım kapsamı dışında.",
            code=INVALID_SCOPE,
        )
    if material.tracking_mode != Material.TrackingMode.SERIALIZED:
        raise ValidationError(
            "Tekil sayım satırı için SERIALIZED malzeme zorunludur.",
            code=INVALID_LINE,
        )
    if not location.active or not location.can_hold_stock:
        raise ValidationError(
            "Sayım lokasyonu aktif ve stok tutabilir olmalıdır.",
            code=INVALID_SCOPE,
        )
    if not condition.active:
        raise ValidationError("Malzeme kondisyonu aktif olmalıdır.", code=INVALID_LINE)


def _apply_serialized_cas(line, expected_counted_at) -> None:
    if line.counted_at != expected_counted_at:
        raise ValidationError(
            "Sayım satırı başka bir kullanıcı tarafından güncellendi.",
            code=COUNT_CONFLICT,
        )


def _next_counted_at(previous):
    counted_at = timezone.now()
    if previous is not None and counted_at <= previous:
        counted_at = previous + timedelta(microseconds=1)
    return counted_at


def _serialized_presence_resolution(line) -> str:
    if (
        line.expected_present
        and line.observed_location_id == line.expected_location_id
        and line.observed_condition_id == line.expected_condition_id
    ):
        return PhysicalCountSerializedLine.ResolutionStatus.NO_DISCREPANCY
    return PhysicalCountSerializedLine.ResolutionStatus.PENDING_APPROVAL


def _create_unexpected_serialized_line(
    *, session, actor, asset, location, condition, using
) -> PhysicalCountSerializedLine:
    line = PhysicalCountSerializedLine(
        session=session,
        serialized_asset=asset,
        material_id=asset.material_id,
        internal_asset_code=asset.internal_asset_code,
        serial_number=asset.serial_number,
        expected_present=False,
        expected_location=None,
        expected_condition=None,
        observed_present=True,
        observed_location=location,
        observed_condition=condition,
        counted_by_user=actor,
        counted_at=timezone.now(),
        resolution_status=PhysicalCountSerializedLine.ResolutionStatus.PENDING_APPROVAL,
    )
    return _save_serialized_line(line, using)


def _save_serialized_line(line, using) -> PhysicalCountSerializedLine:
    try:
        with transaction.atomic(using=using):
            line.save(using=using, force_insert=True)
    except IntegrityError as exc:
        constraint_name = _constraint_name(exc)
        if constraint_name in {
            "counting_sline_session_code_uniq",
            "counting_sline_session_asset_uniq",
            "counting_sline_session_serial_uniq",
        }:
            raise ValidationError(
                "Bu tekil kimlik oturumda zaten sayılıyor.",
                code=DUPLICATE_SERIALIZED_IDENTITY,
            ) from exc
        raise
    return line


def _constraint_name(exc: IntegrityError) -> str | None:
    cause = exc.__cause__
    diag = getattr(cause, "diag", None) if cause is not None else None
    return getattr(diag, "constraint_name", None)
