from __future__ import annotations

from decimal import Decimal

from counting.models import (
    PhysicalCountQuantityLine,
    PhysicalCountSerializedLine,
    PhysicalCountSession,
)
from counting.queries import load_location_parent_map, subtrees_overlap
from imports.models import InventoryBaselineCountSessionLink
from inventory.models import SerializedAsset, StockBalance
from inventory.services.baselines import bucket_has_ledger_history
from inventory.services.receipts import (
    normalize_internal_asset_code,
    normalize_serial_number,
)


def collect_baseline_readiness(
    sessions, *, actor=None, current_baseline=None
) -> list[str]:
    """Surface observable blockers using existing domain queries.

    Establishment remains authoritative; this list is a readiness preview.
    """
    issues: list[str] = []
    session_list = list(sessions)
    if not session_list:
        issues.append("Kesim en az bir sayım oturumu gerektirir.")
        return issues

    parent_map = load_location_parent_map()
    for session in session_list:
        if not session.baseline_candidate:
            issues.append(
                f"{session.reference_number}: yalnız kesim adayı oturum seçilebilir."
            )
        if session.status != PhysicalCountSession.Status.COMPLETED:
            issues.append(
                f"{session.reference_number}: kesim için tamamlanmış sayım zorunludur."
            )
        other_links = InventoryBaselineCountSessionLink.objects.filter(
            physical_count_session_id=session.pk
        )
        if current_baseline is not None:
            other_links = other_links.exclude(
                inventory_baseline_id=current_baseline.pk
            )
        if other_links.exists():
            issues.append(
                f"{session.reference_number}: oturum başka bir kesime bağlı."
            )

    for index, first in enumerate(session_list):
        for second in session_list[index + 1 :]:
            if subtrees_overlap(
                first.scope_location_id,
                second.scope_location_id,
                parent_map=parent_map,
            ):
                issues.append(
                    f"{first.reference_number} ile {second.reference_number} kapsamları çakışıyor."
                )

    quantity_lines = PhysicalCountQuantityLine.objects.filter(
        session__in=session_list
    ).select_related("session", "material", "location", "condition")
    serialized_lines = PhysicalCountSerializedLine.objects.filter(
        session__in=session_list
    ).select_related("session", "material", "serialized_asset")

    blocking_qty = {
        PhysicalCountQuantityLine.ResolutionStatus.PENDING_COUNT,
        PhysicalCountQuantityLine.ResolutionStatus.NOT_COUNTED,
    }
    blocking_ser = {
        PhysicalCountSerializedLine.ResolutionStatus.PENDING_COUNT,
        PhysicalCountSerializedLine.ResolutionStatus.NOT_COUNTED,
    }
    for line in quantity_lines:
        if line.resolution_status in blocking_qty:
            issues.append(
                f"{line.session.reference_number}: zorunlu miktar satırı sayılmamış "
                f"({line.material.material_code} / {line.location.code})."
            )
        elif line.counted_quantity is not None and bucket_has_ledger_history(
            material_id=line.material_id,
            location_id=line.location_id,
            condition_id=line.condition_id,
        ):
            if line.counted_quantity != line.expected_quantity:
                issues.append(
                    f"{line.material.material_code} / {line.location.code}: "
                    "ledger geçmişi olan kova açılış bakiyesi olamaz."
                )
        if (
            actor is not None
            and line.counted_quantity not in (None, Decimal("0"))
            and line.counted_by_user_id == actor.pk
            and not bucket_has_ledger_history(
                material_id=line.material_id,
                location_id=line.location_id,
                condition_id=line.condition_id,
            )
        ):
            issues.append(
                "Sayımı yapan kullanıcı kendi açılış farkını kesemez."
            )

    for line in serialized_lines:
        if line.resolution_status in blocking_ser:
            issues.append(
                f"{line.session.reference_number}: zorunlu tekil satır sayılmamış "
                f"({line.internal_asset_code})."
            )
        elif line.expected_present and line.resolution_status != (
            PhysicalCountSerializedLine.ResolutionStatus.NO_DISCREPANCY
        ):
            issues.append(
                f"{line.internal_asset_code}: beklenen tekil varlık eksik veya "
                "yanlış konum/kondisyonda."
            )
        elif not line.expected_present:
            code = normalize_internal_asset_code(line.internal_asset_code)
            serial = normalize_serial_number(line.serial_number)
            if SerializedAsset.objects.filter(internal_asset_code=code).exists():
                issues.append(
                    f"{code}: aday dahili varlık kodu yetkili varlıkla çakışıyor."
                )
            if serial is not None and SerializedAsset.objects.filter(
                material_id=line.material_id, serial_number=serial
            ).exists():
                issues.append(
                    f"{code}: aday seri numarası bu malzeme için zaten kullanılıyor."
                )
            if actor is not None and line.counted_by_user_id == actor.pk:
                issues.append(
                    "Sayımı yapan kullanıcı kendi açılış farkını kesemez."
                )

    _append_quantity_drift_preview(session_list, quantity_lines, issues)
    # Deduplicate while preserving order.
    unique: list[str] = []
    seen: set[str] = set()
    for issue in issues:
        if issue not in seen:
            seen.add(issue)
            unique.append(issue)
    return unique


def _append_quantity_drift_preview(sessions, quantity_lines, issues) -> None:
    from counting.queries import resolve_location_subtree_ids

    parent_map = load_location_parent_map()
    lines_by_session: dict = {}
    for line in quantity_lines:
        lines_by_session.setdefault(line.session_id, []).append(line)
    for session in sessions:
        subtree = set(
            resolve_location_subtree_ids(
                session.scope_location_id, parent_map=parent_map
            )
        )
        expected = {
            (line.material_id, line.location_id, line.condition_id): line.expected_quantity
            for line in lines_by_session.get(session.pk, ())
            if line.expected_quantity > Decimal("0.000")
        }
        current = {}
        for balance in StockBalance.objects.filter(
            location_id__in=subtree, quantity__gt=Decimal("0")
        ):
            current[
                (balance.material_id, balance.location_id, balance.condition_id)
            ] = balance.quantity
        if expected != current:
            issues.append(
                f"{session.reference_number}: güncel miktar stok sayım başlangıç "
                "snapshot'ından farklı; yeniden sayım gerekir."
            )
