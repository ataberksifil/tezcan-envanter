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
from counting.models import PhysicalCountQuantityLine, PhysicalCountSession
from counting.queries import (
    load_location_parent_map,
    resolve_location_subtree_ids,
    subtrees_overlap,
)
from inventory.models import StockBalance
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
INCOMPLETE_COUNT = "counting.incomplete_count"

QUANTITY_QUANTUM = Decimal("0.001")
MAX_QUANTITY = Decimal("999999999999999.999")


@dataclass(frozen=True)
class CountSessionStartResult:
    session: PhysicalCountSession
    quantity_lines: tuple[PhysicalCountQuantityLine, ...]


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
    """Atomically capture the QUANTITY snapshot and transition DRAFT -> STARTED.

    Counting lock order is session -> Material -> Location -> MaterialCondition ->
    StockBalance. All locks are transaction-scoped; inventory remains unfrozen after
    this short snapshot transaction commits.
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
        if session.quantity_lines.using(using).exists():
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

        _lock_all_quantity_materials(using)
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

        started_at = timezone.now()
        session.status = PhysicalCountSession.Status.STARTED
        session.started_by_user = current_actor
        session.started_at = started_at
        session.save(
            using=using,
            update_fields=["status", "started_by_user", "started_at", "updated_at"],
        )
        return CountSessionStartResult(session=session, quantity_lines=lines)


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
        identity = _line_identity(normalized_line_id, normalized_session_id, using)
        _lock_material(identity[0], using)
        _lock_location(identity[1], using)
        _lock_condition(identity[2], using)
        line = _locked_line(normalized_line_id, normalized_session_id, using)

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
        identity = _line_identity(normalized_line_id, normalized_session_id, using)
        _lock_material(identity[0], using)
        _lock_location(identity[1], using)
        _lock_condition(identity[2], using)
        line = _locked_line(normalized_line_id, normalized_session_id, using)

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
        if session.quantity_lines.using(using).filter(
            resolution_status__in=blocking_statuses
        ).exists():
            message = (
                "Baseline adayı sayımda sayılmamış zorunlu satır kalamaz."
                if session.baseline_candidate
                else "Sayım tamamlanmadan önce tüm zorunlu satırlar sayılmalıdır."
            )
            raise ValidationError(message, code=INCOMPLETE_COUNT)

        completed_at = timezone.now()
        session.status = PhysicalCountSession.Status.COMPLETED
        session.completed_by_user = current_actor
        session.completed_at = completed_at
        session.save(
            using=using,
            update_fields=[
                "status",
                "completed_by_user",
                "completed_at",
                "updated_at",
            ],
        )
        return session


def _current_actor(actor, using: str):
    # Phase 5.4B intentionally does not create/roll out count permissions (5.4E).
    # We still require a saved, active, same-database application actor.
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


def _line_identity(line_id, session_id, using) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    try:
        return (
            PhysicalCountQuantityLine.objects.using(using)
            .filter(pk=line_id, session_id=session_id)
            .values_list("material_id", "location_id", "condition_id")
            .get()
        )
    except PhysicalCountQuantityLine.DoesNotExist as exc:
        raise ValidationError("Sayım satırı bulunamadı.", code=INVALID_LINE) from exc


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


def _lock_all_quantity_materials(using: str) -> None:
    list(
        Material.objects.using(using)
        .select_for_update()
        .filter(tracking_mode=Material.TrackingMode.QUANTITY)
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


def _constraint_name(exc: IntegrityError) -> str | None:
    cause = exc.__cause__
    diag = getattr(cause, "diag", None) if cause is not None else None
    return getattr(diag, "constraint_name", None)
