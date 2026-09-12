"""Read-only inventory transaction history query helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, time

from django.db.models import Prefetch, Q, QuerySet
from django.utils import timezone

from catalog.models import Material, MaterialCondition
from inventory.models import InventoryTransaction, InventoryTransactionLine
from locations.models import Location

TRANSACTION_HISTORY_PAGE_SIZE = 50
TRANSACTION_HISTORY_PERMISSION = "inventory.view_inventorytransaction"
MATERIAL_RECENT_TRANSACTIONS_LIMIT = 10

ALLOWED_TRANSACTION_TYPE_FILTERS = frozenset(
    {
        InventoryTransaction.TransactionType.RECEIPT,
        InventoryTransaction.TransactionType.ISSUE,
    }
)

LINE_PREFETCH = Prefetch(
    "lines",
    queryset=InventoryTransactionLine.objects.select_related(
        "material",
        "unit",
        "condition",
        "source_location",
        "target_location",
    ).order_by("line_number"),
)


def normalize_transaction_type_filter(value: str | None) -> str | None:
    if value in ALLOWED_TRANSACTION_TYPE_FILTERS:
        return value
    return None


def normalize_uuid_filter(value: str | None) -> uuid.UUID | None:
    if not value or not value.strip():
        return None
    try:
        return uuid.UUID(value.strip())
    except ValueError:
        return None


def normalize_actor_filter(value: str | None) -> int | None:
    if not value or not value.strip():
        return None
    try:
        actor_id = int(value.strip())
    except ValueError:
        return None
    if actor_id <= 0:
        return None
    return actor_id


def normalize_date_filter(value: str | None):
    if not value or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def base_transaction_history_queryset() -> QuerySet[InventoryTransaction]:
    return (
        InventoryTransaction.objects.filter(
            transaction_type__in=ALLOWED_TRANSACTION_TYPE_FILTERS,
        )
        .select_related("acting_user", "issue_context")
        .prefetch_related(LINE_PREFETCH)
        .order_by("-occurred_at", "-id")
    )


def apply_transaction_history_filters(
    queryset: QuerySet[InventoryTransaction],
    *,
    q: str = "",
    transaction_type: str | None = None,
    material_id: uuid.UUID | None = None,
    condition_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    actor_id: int | None = None,
    date_from=None,
    date_to=None,
) -> QuerySet[InventoryTransaction]:
    normalized_type = normalize_transaction_type_filter(transaction_type)
    if normalized_type is not None:
        queryset = queryset.filter(transaction_type=normalized_type)

    if material_id is not None:
        queryset = queryset.filter(lines__material_id=material_id).distinct()

    if condition_id is not None:
        queryset = queryset.filter(lines__condition_id=condition_id).distinct()

    if location_id is not None:
        queryset = queryset.filter(
            Q(
                transaction_type=InventoryTransaction.TransactionType.RECEIPT,
                lines__target_location_id=location_id,
            )
            | Q(
                transaction_type=InventoryTransaction.TransactionType.ISSUE,
                lines__source_location_id=location_id,
            )
        ).distinct()

    if actor_id is not None:
        queryset = queryset.filter(acting_user_id=actor_id)

    if date_from is not None:
        start = timezone.make_aware(datetime.combine(date_from, time.min))
        queryset = queryset.filter(occurred_at__gte=start)

    if date_to is not None:
        end = timezone.make_aware(datetime.combine(date_to, time.max))
        queryset = queryset.filter(occurred_at__lte=end)

    if q:
        queryset = queryset.filter(
            Q(lines__material__material_code__icontains=q)
            | Q(lines__material__name__icontains=q)
            | Q(acting_user__username__icontains=q)
            | Q(acting_user__first_name__icontains=q)
            | Q(acting_user__last_name__icontains=q)
            | Q(issue_context__receiver_employee_number_snapshot__icontains=q)
            | Q(issue_context__receiver_first_name_snapshot__icontains=q)
            | Q(issue_context__receiver_last_name_snapshot__icontains=q)
            | Q(issue_context__usage_location_text__icontains=q)
        ).distinct()

    return queryset


def filter_params_from_request(request) -> dict:
    return {
        "q": request.GET.get("q", "").strip(),
        "transaction_type": request.GET.get("transaction_type", "").strip() or None,
        "material_id": normalize_uuid_filter(request.GET.get("material")),
        "condition_id": normalize_uuid_filter(request.GET.get("condition")),
        "location_id": normalize_uuid_filter(request.GET.get("location")),
        "actor_id": normalize_actor_filter(request.GET.get("actor")),
        "date_from": normalize_date_filter(request.GET.get("date_from")),
        "date_to": normalize_date_filter(request.GET.get("date_to")),
    }


def build_transaction_history_queryset(request) -> QuerySet[InventoryTransaction]:
    params = filter_params_from_request(request)
    queryset = base_transaction_history_queryset()
    return apply_transaction_history_filters(queryset, **params)


def recent_transactions_for_material(material_id: uuid.UUID) -> QuerySet[InventoryTransaction]:
    return base_transaction_history_queryset().filter(lines__material_id=material_id).distinct()[
        :MATERIAL_RECENT_TRANSACTIONS_LIMIT
    ]


def filter_form_context() -> dict:
    return {
        "filter_materials": Material.objects.order_by("name", "material_code", "id"),
        "filter_conditions": MaterialCondition.objects.order_by("sort_order", "name", "id"),
        "filter_locations": Location.objects.order_by("code", "id"),
        "filter_actors": (
            InventoryTransaction.objects.filter(
                transaction_type__in=ALLOWED_TRANSACTION_TYPE_FILTERS,
            )
            .select_related("acting_user")
            .order_by("acting_user__username", "acting_user__id")
            .values_list("acting_user_id", "acting_user__username")
            .distinct()
        ),
        "transaction_type_choices": [
            (InventoryTransaction.TransactionType.RECEIPT, "Stok girişi"),
            (InventoryTransaction.TransactionType.ISSUE, "Stok çıkışı"),
        ],
    }
