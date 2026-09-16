from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connections, transaction
from django.utils import timezone

from audit.services import record_audit_event
from catalog.models import Material, MaterialCondition
from counting.models import (
    PhysicalCountQuantityLine,
    PhysicalCountSerializedLine,
    PhysicalCountSession,
)
from counting.queries import (
    load_location_parent_map,
    resolve_location_subtree_ids,
    subtrees_overlap,
)
from imports.models import (
    InventoryBaseline,
    InventoryBaselineCountSessionLink,
    InventoryBaselineTransactionLink,
)
from inventory.models import (
    InventoryTransaction,
    SerializedAsset,
    StockBalance,
)
from inventory.services.baselines import (
    ESTABLISH_BASELINE_PERMISSION,
    QuantityOpening,
    SerializedOpening,
    bucket_has_ledger_history,
    derive_scoped_operation_id,
    establish_initial_balance,
)
from inventory.services.projections import (
    verify_quantity_projection,
    verify_serialized_projection,
)
from inventory.services.receipts import (
    OPERATION_CONFLICT,
    _normalize_uuid as _inventory_uuid,
    _raise_validation,
    normalize_internal_asset_code,
    normalize_serial_number,
)
from locations.models import Location


INVALID_BASELINE = "imports.invalid_baseline"
INVALID_SESSION = "imports.invalid_session"
OVERLAPPING_SCOPE = "imports.overlapping_scope"
INCOMPLETE_COUNT = "imports.incomplete_count"
COUNT_DRIFT = "imports.count_drift"
PRIOR_HISTORY = "imports.prior_history"
PROJECTION_INTEGRITY = "imports.projection_integrity"
SERIALIZED_DISCREPANCY = "imports.serialized_discrepancy"
SELF_APPROVAL = "imports.self_approval"
INVALID_EXPLANATION = "imports.invalid_explanation"
IDENTITY_CONFLICT = "imports.identity_conflict"
PROJECTION_MISMATCH = "imports.projection_mismatch"
SESSION_LINK_UNIQUE = "imports_baseline_session_global_uniq"


@dataclass(frozen=True)
class BaselinePrepareResult:
    baseline: InventoryBaseline
    session_links: tuple[InventoryBaselineCountSessionLink, ...]


@dataclass(frozen=True)
class BaselineEstablishResult:
    baseline: InventoryBaseline
    transactions: tuple[InventoryTransaction, ...]
    replayed: bool


def prepare_inventory_baseline(
    *,
    actor,
    session_ids,
    reference=None,
    using: str = "default",
) -> BaselinePrepareResult:
    """Create a non-authoritative baseline from selected completed count sessions."""
    current_actor = _authorize(actor, using)
    session_uuids = _normalize_session_ids(session_ids)
    reference_value = _normalize_reference(reference)

    with transaction.atomic(using=using):
        sessions = _locked_sessions(session_uuids, using)
        _validate_prepare_sessions(sessions, using)
        baseline = InventoryBaseline(
            reference=reference_value,
            status=InventoryBaseline.Status.PREPARED,
            created_by=current_actor,
        )
        baseline._state.db = using
        baseline.full_clean(validate_constraints=False)
        try:
            with transaction.atomic(using=using):
                baseline.save(using=using, force_insert=True)
        except IntegrityError as exc:
            if _constraint_name(exc) == "imports_baseline_reference_uniq":
                raise ValidationError(
                    "Bu baseline referansı zaten kullanılıyor.",
                    code=INVALID_BASELINE,
                ) from exc
            raise
        links = []
        for session in sessions:
            link = InventoryBaselineCountSessionLink(
                inventory_baseline=baseline,
                physical_count_session=session,
                required=True,
            )
            try:
                with transaction.atomic(using=using):
                    link.save(using=using, force_insert=True)
            except IntegrityError as exc:
                if _constraint_name(exc) == SESSION_LINK_UNIQUE:
                    raise ValidationError(
                        "Sayım oturumu başka bir baseline'a bağlı.",
                        code=INVALID_SESSION,
                    ) from exc
                raise
            links.append(link)
        return BaselinePrepareResult(baseline=baseline, session_links=tuple(links))


def establish_inventory_baseline(
    *,
    actor,
    baseline_id,
    operation_id,
    explanation,
    using: str = "default",
) -> BaselineEstablishResult:
    """Atomically convert a prepared baseline into authoritative opening inventory.

    Lock order: baseline -> sessions (pk) -> quantity lines (pk) -> serialized
    lines (pk) -> Material (pk) -> Location (pk) -> MaterialCondition (pk) ->
    StockBalance (material, location, condition, pk) -> serialized_assets SHARE
    -> SerializedAsset (pk) -> child INITIAL_BALANCE operation reservation.
    """
    current_actor = _authorize(actor, using)
    baseline_uuid = _normalize_uuid(baseline_id, code=INVALID_BASELINE)
    operation_uuid = _inventory_uuid(
        operation_id, field="operation_id", code="inventory.invalid_operation_id"
    )
    normalized_explanation = _normalize_explanation(explanation)

    with transaction.atomic(using=using):
        baseline = _locked_baseline(baseline_uuid, using)
        links = tuple(
            InventoryBaselineCountSessionLink.objects.using(using)
            .select_related("physical_count_session")
            .filter(inventory_baseline_id=baseline.pk)
            .order_by("physical_count_session_id")
        )
        if not links:
            raise ValidationError(
                "Baseline en az bir sayım oturumu gerektirir.",
                code=INVALID_BASELINE,
            )
        session_ids = tuple(link.physical_count_session_id for link in links)
        sessions = _locked_sessions(session_ids, using)
        quantity_lines = _locked_quantity_lines(session_ids, using)
        serialized_lines = _locked_serialized_lines(session_ids, using)
        fingerprint = _establishment_fingerprint(
            acting_user_id=current_actor.pk,
            baseline_id=baseline.pk,
            explanation=normalized_explanation,
            sessions=sessions,
            quantity_lines=quantity_lines,
            serialized_lines=serialized_lines,
        )

        if baseline.status == InventoryBaseline.Status.ESTABLISHED:
            return _replay_or_conflict(
                baseline=baseline,
                operation_id=operation_uuid,
                fingerprint=fingerprint,
                using=using,
            )
        if baseline.status != InventoryBaseline.Status.PREPARED:
            raise ValidationError("Baseline kesime uygun değil.", code=INVALID_BASELINE)

        if (
            baseline.establishment_operation_id is not None
            and baseline.establishment_operation_id != operation_uuid
        ):
            _raise_validation(
                OPERATION_CONFLICT,
                "operation_id farklı bir envanter isteği için zaten kullanılmış.",
            )

        _validate_establish_sessions(sessions, using)
        _reject_incomplete_evidence(quantity_lines, serialized_lines)

        parent_map = load_location_parent_map(using=using)
        session_subtrees = {
            session.pk: resolve_location_subtree_ids(
                session.scope_location_id, parent_map=parent_map, using=using
            )
            for session in sessions
        }
        all_location_ids = tuple(
            sorted({location_id for subtree in session_subtrees.values() for location_id in subtree}, key=str)
        )
        material_ids = _collect_material_ids(
            quantity_lines, serialized_lines, all_location_ids, using
        )
        condition_ids = _collect_condition_ids(
            quantity_lines, serialized_lines, all_location_ids, using
        )

        for material_id in material_ids:
            _locked_material(material_id, using)
        for location_id in all_location_ids:
            _locked_location(location_id, using)
        extra_locations = sorted(
            {
                *(line.location_id for line in quantity_lines),
                *(line.observed_location_id for line in serialized_lines if line.observed_location_id),
                *(line.expected_location_id for line in serialized_lines if line.expected_location_id),
            }
            - set(all_location_ids),
            key=str,
        )
        for location_id in extra_locations:
            _locked_location(location_id, using)
        for condition_id in condition_ids:
            _locked_condition(condition_id, using)

        balances = tuple(
            StockBalance.objects.using(using)
            .select_for_update()
            .filter(location_id__in=all_location_ids)
            .order_by("material_id", "location_id", "condition_id", "pk")
        )
        _lock_serialized_asset_table(using)
        in_scope_assets = tuple(
            SerializedAsset.objects.using(using)
            .select_for_update()
            .filter(
                current_state=SerializedAsset.CurrentState.IN_STOCK,
                current_location_id__in=all_location_ids,
            )
            .order_by("pk")
        )
        expected_asset_ids = tuple(
            sorted(
                {
                    line.serialized_asset_id
                    for line in serialized_lines
                    if line.serialized_asset_id is not None
                },
                key=str,
            )
        )
        extra_asset_ids = [
            asset_id
            for asset_id in expected_asset_ids
            if asset_id not in {asset.pk for asset in in_scope_assets}
        ]
        extra_assets = tuple(
            SerializedAsset.objects.using(using)
            .select_for_update()
            .filter(pk__in=extra_asset_ids)
            .order_by("pk")
        )
        assets_by_id = {asset.pk: asset for asset in in_scope_assets}
        assets_by_id.update({asset.pk: asset for asset in extra_assets})

        _reject_quantity_drift(
            sessions=sessions,
            session_subtrees=session_subtrees,
            quantity_lines=quantity_lines,
            balances=balances,
        )
        _reject_serialized_drift(
            sessions=sessions,
            session_subtrees=session_subtrees,
            serialized_lines=serialized_lines,
            in_scope_assets=in_scope_assets,
            assets_by_id=assets_by_id,
        )

        openings_by_session = _plan_openings(
            sessions=sessions,
            current_actor=current_actor,
            quantity_lines=quantity_lines,
            serialized_lines=serialized_lines,
            balances=balances,
            using=using,
        )

        created_transactions: list[InventoryTransaction] = []
        for session in sessions:
            quantity_openings, serialized_openings = openings_by_session[session.pk]
            if not quantity_openings and not serialized_openings:
                continue
            child_operation_id = derive_scoped_operation_id(operation_uuid, session.pk)
            mutation = establish_initial_balance(
                actor=current_actor,
                operation_id=child_operation_id,
                quantity_openings=quantity_openings,
                serialized_openings=serialized_openings,
                using=using,
            )
            InventoryBaselineTransactionLink.objects.using(using).create(
                inventory_baseline=baseline,
                inventory_transaction=mutation.transaction,
                physical_count_session=session,
                scope_key=str(session.pk),
                operation_id=child_operation_id,
            )
            created_transactions.append(mutation.transaction)

        quantity_mismatches = verify_quantity_projection(using=using)
        serialized_mismatches = verify_serialized_projection(using=using)
        if quantity_mismatches or serialized_mismatches:
            raise ValidationError(
                "Açılış sonrası projeksiyon ledger ile uyuşmuyor.",
                code=PROJECTION_MISMATCH,
            )

        now = timezone.now()
        baseline.status = InventoryBaseline.Status.ESTABLISHED
        baseline.established_by = current_actor
        baseline.established_at = now
        baseline.establishment_operation_id = operation_uuid
        baseline.request_fingerprint = fingerprint
        baseline.establishment_explanation = normalized_explanation
        baseline.full_clean(validate_constraints=False)
        baseline.save(
            using=using,
            update_fields=[
                "status",
                "established_by",
                "established_at",
                "establishment_operation_id",
                "request_fingerprint",
                "establishment_explanation",
            ],
        )
        record_audit_event(
            actor=current_actor,
            event_type="InventoryBaselineEstablished",
            entity_type="InventoryBaseline",
            entity_id=baseline.pk,
            before_data={"status": InventoryBaseline.Status.PREPARED},
            after_data={
                "status": InventoryBaseline.Status.ESTABLISHED,
                "transaction_ids": [str(tx.pk) for tx in created_transactions],
                "session_ids": [str(session.pk) for session in sessions],
            },
            metadata={"explanation": normalized_explanation},
            using=using,
        )
        return BaselineEstablishResult(
            baseline=baseline,
            transactions=tuple(created_transactions),
            replayed=False,
        )


def _authorize(actor, using):
    if actor is None or getattr(actor, "pk", None) is None or actor._state.db != using:
        raise PermissionDenied
    user_model = get_user_model()
    try:
        current = user_model.objects.using(using).get(pk=actor.pk)
    except user_model.DoesNotExist as exc:
        raise PermissionDenied from exc
    if not current.is_active or not current.has_perm(ESTABLISH_BASELINE_PERMISSION):
        raise PermissionDenied
    return current


def _normalize_uuid(value, *, code: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Geçersiz kimlik.", code=code) from exc


def _normalize_session_ids(session_ids) -> tuple[uuid.UUID, ...]:
    if session_ids is None:
        raise ValidationError("En az bir sayım oturumu seçilmelidir.", code=INVALID_SESSION)
    try:
        values = tuple(session_ids)
    except TypeError as exc:
        raise ValidationError("Sayım oturumu listesi geçersiz.", code=INVALID_SESSION) from exc
    if not values:
        raise ValidationError("En az bir sayım oturumu seçilmelidir.", code=INVALID_SESSION)
    unique: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for value in values:
        session_id = _normalize_uuid(value, code=INVALID_SESSION)
        if session_id in seen:
            raise ValidationError(
                "Aynı sayım oturumu birden fazla seçilemez.",
                code=INVALID_SESSION,
            )
        seen.add(session_id)
        unique.append(session_id)
    return tuple(sorted(unique, key=str))


def _normalize_reference(value) -> str:
    if value is None:
        return f"BL-{uuid.uuid4().hex[:12]}"
    if not isinstance(value, str):
        raise ValidationError("Baseline referansı metin olmalıdır.", code=INVALID_BASELINE)
    normalized = value.strip()
    if not normalized or len(normalized) > 64:
        raise ValidationError(
            "Baseline referansı 1 ile 64 karakter arasında olmalıdır.",
            code=INVALID_BASELINE,
        )
    return normalized


def _normalize_explanation(value) -> str:
    if not isinstance(value, str):
        raise ValidationError("Kesim açıklaması zorunludur.", code=INVALID_EXPLANATION)
    normalized = value.strip()
    if not 10 <= len(normalized) <= 2000:
        raise ValidationError(
            "Kesim açıklaması 10 ile 2000 karakter arasında olmalıdır.",
            code=INVALID_EXPLANATION,
        )
    return normalized


def _locked_baseline(baseline_id, using) -> InventoryBaseline:
    try:
        return (
            InventoryBaseline.objects.using(using)
            .select_for_update()
            .get(pk=baseline_id)
        )
    except InventoryBaseline.DoesNotExist as exc:
        raise ValidationError("Baseline bulunamadı.", code=INVALID_BASELINE) from exc


def _locked_sessions(session_ids, using) -> tuple[PhysicalCountSession, ...]:
    sessions = tuple(
        PhysicalCountSession.objects.using(using)
        .select_for_update()
        .filter(pk__in=session_ids)
        .order_by("pk")
    )
    if len(sessions) != len(session_ids):
        raise ValidationError("Sayım oturumu bulunamadı.", code=INVALID_SESSION)
    return sessions


def _locked_quantity_lines(session_ids, using) -> tuple[PhysicalCountQuantityLine, ...]:
    return tuple(
        PhysicalCountQuantityLine.objects.using(using)
        .select_for_update()
        .filter(session_id__in=session_ids)
        .order_by("pk")
    )


def _locked_serialized_lines(session_ids, using) -> tuple[PhysicalCountSerializedLine, ...]:
    return tuple(
        PhysicalCountSerializedLine.objects.using(using)
        .select_for_update()
        .filter(session_id__in=session_ids)
        .order_by("pk")
    )


def _validate_prepare_sessions(sessions, using) -> None:
    parent_map = load_location_parent_map(using=using)
    for session in sessions:
        if not session.baseline_candidate:
            raise ValidationError(
                "Yalnız baseline adayı sayım oturumu seçilebilir.",
                code=INVALID_SESSION,
            )
        if session.status != PhysicalCountSession.Status.COMPLETED:
            raise ValidationError(
                "Kesim için tamamlanmış sayım oturumu zorunludur.",
                code=INVALID_SESSION,
            )
        if InventoryBaselineCountSessionLink.objects.using(using).filter(
            physical_count_session_id=session.pk
        ).exists():
            raise ValidationError(
                "Sayım oturumu başka bir baseline'a bağlı.",
                code=INVALID_SESSION,
            )
    for index, first in enumerate(sessions):
        for second in sessions[index + 1 :]:
            if subtrees_overlap(
                first.scope_location_id,
                second.scope_location_id,
                parent_map=parent_map,
            ):
                raise ValidationError(
                    "Seçilen sayım kapsamları çakışamaz.",
                    code=OVERLAPPING_SCOPE,
                )


def _validate_establish_sessions(sessions, using) -> None:
    _validate_prepare_sessions_relaxed(sessions, using)


def _validate_prepare_sessions_relaxed(sessions, using) -> None:
    parent_map = load_location_parent_map(using=using)
    for session in sessions:
        if not session.baseline_candidate:
            raise ValidationError(
                "Yalnız baseline adayı sayım oturumu seçilebilir.",
                code=INVALID_SESSION,
            )
        if session.status != PhysicalCountSession.Status.COMPLETED:
            raise ValidationError(
                "Kesim için tamamlanmış sayım oturumu zorunludur.",
                code=INVALID_SESSION,
            )
    for index, first in enumerate(sessions):
        for second in sessions[index + 1 :]:
            if subtrees_overlap(
                first.scope_location_id,
                second.scope_location_id,
                parent_map=parent_map,
            ):
                raise ValidationError(
                    "Seçilen sayım kapsamları çakışamaz.",
                    code=OVERLAPPING_SCOPE,
                )


def _reject_incomplete_evidence(quantity_lines, serialized_lines) -> None:
    blocking = {
        PhysicalCountQuantityLine.ResolutionStatus.PENDING_COUNT,
        PhysicalCountQuantityLine.ResolutionStatus.NOT_COUNTED,
    }
    if any(line.resolution_status in blocking for line in quantity_lines):
        raise ValidationError(
            "Baseline için sayılmamış zorunlu miktar satırı kalamaz.",
            code=INCOMPLETE_COUNT,
        )
    serialized_blocking = {
        PhysicalCountSerializedLine.ResolutionStatus.PENDING_COUNT,
        PhysicalCountSerializedLine.ResolutionStatus.NOT_COUNTED,
    }
    if any(line.resolution_status in serialized_blocking for line in serialized_lines):
        raise ValidationError(
            "Baseline için sayılmamış zorunlu tekil satır kalamaz.",
            code=INCOMPLETE_COUNT,
        )


def _collect_material_ids(quantity_lines, serialized_lines, location_ids, using):
    ids = {
        *(line.material_id for line in quantity_lines),
        *(line.material_id for line in serialized_lines),
        *StockBalance.objects.using(using)
        .filter(location_id__in=location_ids)
        .values_list("material_id", flat=True),
        *SerializedAsset.objects.using(using)
        .filter(current_location_id__in=location_ids)
        .values_list("material_id", flat=True),
    }
    return tuple(sorted(ids, key=str))


def _collect_condition_ids(quantity_lines, serialized_lines, location_ids, using):
    ids = {
        *(line.condition_id for line in quantity_lines),
        *(line.observed_condition_id for line in serialized_lines if line.observed_condition_id),
        *(line.expected_condition_id for line in serialized_lines if line.expected_condition_id),
        *StockBalance.objects.using(using)
        .filter(location_id__in=location_ids)
        .values_list("condition_id", flat=True),
        *SerializedAsset.objects.using(using)
        .filter(current_location_id__in=location_ids)
        .values_list("current_condition_id", flat=True),
    }
    return tuple(sorted(ids, key=str))


def _locked_material(material_id, using) -> Material:
    return Material.objects.using(using).select_for_update().get(pk=material_id)


def _locked_location(location_id, using) -> Location:
    return Location.objects.using(using).select_for_update().get(pk=location_id)


def _locked_condition(condition_id, using) -> MaterialCondition:
    return MaterialCondition.objects.using(using).select_for_update().get(pk=condition_id)


def _lock_serialized_asset_table(using: str) -> None:
    with connections[using].cursor() as cursor:
        cursor.execute("LOCK TABLE inventory_serializedasset IN EXCLUSIVE MODE")


def _reject_quantity_drift(*, sessions, session_subtrees, quantity_lines, balances) -> None:
    lines_by_session: dict[uuid.UUID, list[PhysicalCountQuantityLine]] = {}
    for line in quantity_lines:
        lines_by_session.setdefault(line.session_id, []).append(line)
    balances_by_location: dict[uuid.UUID, list[StockBalance]] = {}
    for balance in balances:
        balances_by_location.setdefault(balance.location_id, []).append(balance)

    for session in sessions:
        subtree = set(session_subtrees[session.pk])
        expected = {
            (line.material_id, line.location_id, line.condition_id): line.expected_quantity
            for line in lines_by_session.get(session.pk, ())
            if line.expected_quantity > Decimal("0.000")
        }
        current = {}
        for location_id in subtree:
            for balance in balances_by_location.get(location_id, ()):
                if balance.quantity > Decimal("0.000"):
                    current[(balance.material_id, balance.location_id, balance.condition_id)] = (
                        balance.quantity
                    )
        if expected != current:
            raise ValidationError(
                "Güncel miktar stok sayım başlangıç snapshot'ından farklı; yeniden sayım gerekir.",
                code=COUNT_DRIFT,
            )


def _reject_serialized_drift(
    *, sessions, session_subtrees, serialized_lines, in_scope_assets, assets_by_id
) -> None:
    lines_by_session: dict[uuid.UUID, list[PhysicalCountSerializedLine]] = {}
    for line in serialized_lines:
        lines_by_session.setdefault(line.session_id, []).append(line)
    assets_by_location: dict[uuid.UUID, list[SerializedAsset]] = {}
    for asset in in_scope_assets:
        assets_by_location.setdefault(asset.current_location_id, []).append(asset)

    for session in sessions:
        subtree = set(session_subtrees[session.pk])
        expected_ids = {
            line.serialized_asset_id
            for line in lines_by_session.get(session.pk, ())
            if line.expected_present and line.serialized_asset_id is not None
        }
        current_ids = {
            asset.pk
            for location_id in subtree
            for asset in assets_by_location.get(location_id, ())
        }
        if current_ids != expected_ids:
            raise ValidationError(
                "Güncel tekil envanter sayım başlangıç snapshot'ından farklı; yeniden sayım gerekir.",
                code=COUNT_DRIFT,
            )
        for asset_id in expected_ids:
            asset = assets_by_id.get(asset_id)
            expected_line = next(
                line
                for line in lines_by_session.get(session.pk, ())
                if line.serialized_asset_id == asset_id
            )
            if (
                asset is None
                or asset.current_state != SerializedAsset.CurrentState.IN_STOCK
                or asset.current_location_id != expected_line.expected_location_id
                or asset.current_condition_id != expected_line.expected_condition_id
            ):
                raise ValidationError(
                    "Güncel tekil envanter sayım başlangıç snapshot'ından farklı; yeniden sayım gerekir.",
                    code=COUNT_DRIFT,
                )


def _plan_openings(
    *, sessions, current_actor, quantity_lines, serialized_lines, balances, using
):
    balance_map = {
        (balance.material_id, balance.location_id, balance.condition_id): balance.quantity
        for balance in balances
    }
    openings: dict[uuid.UUID, tuple[list[QuantityOpening], list[SerializedOpening]]] = {
        session.pk: ([], []) for session in sessions
    }

    for line in quantity_lines:
        if line.resolution_status not in {
            PhysicalCountQuantityLine.ResolutionStatus.NO_DISCREPANCY,
            PhysicalCountQuantityLine.ResolutionStatus.PENDING_APPROVAL,
        }:
            raise ValidationError(
                "Baseline için sayılmamış veya çözülmemiş miktar satırı kalamaz.",
                code=INCOMPLETE_COUNT,
            )
        current_qty = balance_map.get(
            (line.material_id, line.location_id, line.condition_id),
            Decimal("0.000"),
        )
        history = bucket_has_ledger_history(
            material_id=line.material_id,
            location_id=line.location_id,
            condition_id=line.condition_id,
            using=using,
        )
        counted = line.counted_quantity
        if counted is None:
            raise ValidationError(
                "Baseline için sayılmamış zorunlu miktar satırı kalamaz.",
                code=INCOMPLETE_COUNT,
            )
        if history:
            if counted != line.expected_quantity:
                raise ValidationError(
                    "Ledger geçmişi olan kova açılış bakiyesi veya sayım mutabakatı ile düzeltilemez.",
                    code=PRIOR_HISTORY,
                )
            continue
        if current_qty > Decimal("0.000"):
            raise ValidationError(
                "Ledger geçmişi olmayan pozitif bakiye projeksiyon hatasıdır.",
                code=PROJECTION_INTEGRITY,
            )
        if counted == Decimal("0.000"):
            continue
        if line.counted_by_user_id == current_actor.pk:
            raise ValidationError(
                "Sayımı yapan kullanıcı kendi açılış farkını kesemez.",
                code=SELF_APPROVAL,
            )
        openings[line.session_id][0].append(
            QuantityOpening(
                material_id=line.material_id,
                location_id=line.location_id,
                condition_id=line.condition_id,
                quantity=counted,
            )
        )

    for line in serialized_lines:
        if line.expected_present:
            if line.resolution_status != PhysicalCountSerializedLine.ResolutionStatus.NO_DISCREPANCY:
                raise ValidationError(
                    "Beklenen tekil varlık eksik veya yanlış konum/kondisyonda; kesim reddedilir.",
                    code=SERIALIZED_DISCREPANCY,
                )
            continue
        if (
            line.serialized_asset_id is not None
            or line.observed_present is not True
            or line.observed_location_id is None
            or line.observed_condition_id is None
        ):
            raise ValidationError(
                "Aday tekil satır açık fiziksel gözlem olmadan kesilemez.",
                code=SERIALIZED_DISCREPANCY,
            )
        code = normalize_internal_asset_code(line.internal_asset_code)
        serial = normalize_serial_number(line.serial_number)
        if SerializedAsset.objects.using(using).filter(internal_asset_code=code).exists():
            raise ValidationError(
                "Aday dahili varlık kodu yetkili bir tekil varlıkla çakışıyor.",
                code=IDENTITY_CONFLICT,
            )
        if serial is not None and SerializedAsset.objects.using(using).filter(
            material_id=line.material_id, serial_number=serial
        ).exists():
            raise ValidationError(
                "Aday üretici seri numarası bu malzeme için zaten kullanılıyor.",
                code=IDENTITY_CONFLICT,
            )
        if line.counted_by_user_id == current_actor.pk:
            raise ValidationError(
                "Sayımı yapan kullanıcı kendi açılış farkını kesemez.",
                code=SELF_APPROVAL,
            )
        openings[line.session_id][1].append(
            SerializedOpening(
                material_id=line.material_id,
                internal_asset_code=code,
                serial_number=serial,
                location_id=line.observed_location_id,
                condition_id=line.observed_condition_id,
            )
        )
    return openings


def _establishment_fingerprint(
    *,
    acting_user_id,
    baseline_id,
    explanation,
    sessions,
    quantity_lines,
    serialized_lines,
) -> str:
    payload = {
        "acting_user_id": str(acting_user_id),
        "baseline_id": str(baseline_id),
        "command": "establish_inventory_baseline",
        "explanation": explanation,
        "sessions": [
            {
                "quantity_lines": [
                    {
                        "counted_quantity": (
                            format(line.counted_quantity, ".3f")
                            if line.counted_quantity is not None
                            else None
                        ),
                        "condition_id": str(line.condition_id),
                        "expected_quantity": format(line.expected_quantity, ".3f"),
                        "id": str(line.pk),
                        "location_id": str(line.location_id),
                        "material_id": str(line.material_id),
                        "resolution_status": line.resolution_status,
                    }
                    for line in sorted(
                        (row for row in quantity_lines if row.session_id == session.pk),
                        key=lambda row: str(row.pk),
                    )
                ],
                "scope_location_id": str(session.scope_location_id),
                "serialized_lines": [
                    {
                        "expected_condition_id": (
                            str(line.expected_condition_id)
                            if line.expected_condition_id
                            else None
                        ),
                        "expected_location_id": (
                            str(line.expected_location_id)
                            if line.expected_location_id
                            else None
                        ),
                        "expected_present": line.expected_present,
                        "id": str(line.pk),
                        "internal_asset_code": line.internal_asset_code,
                        "material_id": str(line.material_id),
                        "observed_condition_id": (
                            str(line.observed_condition_id)
                            if line.observed_condition_id
                            else None
                        ),
                        "observed_location_id": (
                            str(line.observed_location_id)
                            if line.observed_location_id
                            else None
                        ),
                        "observed_present": line.observed_present,
                        "resolution_status": line.resolution_status,
                        "serial_number": line.serial_number,
                        "serialized_asset_id": (
                            str(line.serialized_asset_id)
                            if line.serialized_asset_id
                            else None
                        ),
                    }
                    for line in sorted(
                        (row for row in serialized_lines if row.session_id == session.pk),
                        key=lambda row: str(row.pk),
                    )
                ],
                "session_id": str(session.pk),
            }
            for session in sessions
        ],
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replay_or_conflict(*, baseline, operation_id, fingerprint, using) -> BaselineEstablishResult:
    if (
        baseline.establishment_operation_id != operation_id
        or baseline.request_fingerprint != fingerprint
    ):
        _raise_validation(
            OPERATION_CONFLICT,
            "operation_id farklı bir envanter isteği için zaten kullanılmış.",
        )
    transactions = tuple(
        link.inventory_transaction
        for link in InventoryBaselineTransactionLink.objects.using(using)
        .select_related("inventory_transaction")
        .filter(inventory_baseline_id=baseline.pk)
        .order_by("physical_count_session_id")
    )
    return BaselineEstablishResult(
        baseline=baseline,
        transactions=transactions,
        replayed=True,
    )


def _constraint_name(exc: IntegrityError) -> str | None:
    cause = exc.__cause__
    diag = getattr(cause, "diag", None) if cause is not None else None
    return getattr(diag, "constraint_name", None)
