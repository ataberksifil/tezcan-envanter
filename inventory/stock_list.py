"""Read-only current stock (StockBalance projection) query helpers."""

from __future__ import annotations

import uuid

from django.db.models import Q, QuerySet

from catalog.models import Material, MaterialCondition
from inventory.models import StockBalance
from locations.models import Location

STOCK_LIST_PAGE_SIZE = 50
STOCK_LIST_PERMISSION = "inventory.view_stockbalance"

BALANCE_STATE_POSITIVE = "positive"
BALANCE_STATE_ZERO = "zero"
BALANCE_STATE_ALL = "all"
ALLOWED_BALANCE_STATE_FILTERS = frozenset(
    {BALANCE_STATE_POSITIVE, BALANCE_STATE_ZERO, BALANCE_STATE_ALL}
)


def normalize_balance_state_filter(value: str | None) -> str:
    if value in ALLOWED_BALANCE_STATE_FILTERS:
        return value
    return BALANCE_STATE_POSITIVE


def normalize_uuid_filter(value: str | None) -> uuid.UUID | None:
    if not value or not value.strip():
        return None
    try:
        return uuid.UUID(value.strip())
    except ValueError:
        return None


def base_stock_balance_queryset() -> QuerySet[StockBalance]:
    return (
        StockBalance.objects.select_related(
            "material",
            "material__unit",
            "location",
            "condition",
        )
        .filter(material__tracking_mode=Material.TrackingMode.QUANTITY)
        .order_by(
            "material__name",
            "material__material_code",
            "location__code",
            "location__name",
            "condition__sort_order",
            "condition__name",
            "id",
        )
    )


def apply_stock_balance_filters(
    queryset: QuerySet[StockBalance],
    *,
    q: str = "",
    location_id: uuid.UUID | None = None,
    condition_id: uuid.UUID | None = None,
    balance_state: str = BALANCE_STATE_POSITIVE,
) -> QuerySet[StockBalance]:
    state = normalize_balance_state_filter(balance_state)
    if state == BALANCE_STATE_POSITIVE:
        queryset = queryset.filter(quantity__gt=0)
    elif state == BALANCE_STATE_ZERO:
        queryset = queryset.filter(quantity=0)

    if location_id is not None:
        queryset = queryset.filter(location_id=location_id)

    if condition_id is not None:
        queryset = queryset.filter(condition_id=condition_id)

    if q:
        queryset = queryset.filter(
            Q(material__material_code__icontains=q)
            | Q(material__name__icontains=q)
            | Q(material__brand__icontains=q)
            | Q(material__model__icontains=q)
            | Q(location__code__icontains=q)
            | Q(location__name__icontains=q)
        )

    return queryset


def filter_params_from_request(request) -> dict:
    balance_state_raw = request.GET.get("balance_state", BALANCE_STATE_POSITIVE).strip()
    return {
        "q": request.GET.get("q", "").strip(),
        "location_id": normalize_uuid_filter(request.GET.get("location")),
        "condition_id": normalize_uuid_filter(request.GET.get("condition")),
        "balance_state": normalize_balance_state_filter(balance_state_raw or None),
    }


def build_stock_balance_queryset(request) -> QuerySet[StockBalance]:
    params = filter_params_from_request(request)
    queryset = base_stock_balance_queryset()
    return apply_stock_balance_filters(queryset, **params)


def current_stock_balances_for_material(
    material_id: uuid.UUID,
    *,
    include_zero: bool = False,
) -> QuerySet[StockBalance]:
    queryset = base_stock_balance_queryset().filter(material_id=material_id)
    if not include_zero:
        queryset = queryset.filter(quantity__gt=0)
    return queryset


def stock_list_filter_form_context() -> dict:
    return {
        "filter_locations": Location.objects.order_by("code", "id"),
        "filter_conditions": MaterialCondition.objects.order_by("sort_order", "name", "id"),
        "balance_state_choices": [
            (BALANCE_STATE_POSITIVE, "Pozitif"),
            (BALANCE_STATE_ZERO, "Sıfır"),
            (BALANCE_STATE_ALL, "Tümü"),
        ],
    }
