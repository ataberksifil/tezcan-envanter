"""Read-only current stock (StockBalance projection) query helpers."""

from __future__ import annotations

import uuid

from django.db.models import Count, OuterRef, Q, QuerySet, Subquery, Sum

from catalog.models import Material, MaterialCondition, turkish_casefold, turkish_fold_expr
from inventory.models import SerializedAsset, StockBalance
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
        queryset = apply_stock_text_search(queryset, q)

    return queryset


_MATERIAL_SEARCH_FIELDS = {
    "_stock_search_code": "material__material_code",
    "_stock_search_name": "material__name",
    "_stock_search_brand": "material__brand",
    "_stock_search_model": "material__model",
    "_stock_search_keywords": "material__search_keywords",
}
_LOCATION_SEARCH_FIELDS = {
    "_stock_search_location_code": "location__code",
    "_stock_search_location_name": "location__name",
}


def apply_stock_text_search(queryset: QuerySet[StockBalance], query: str) -> QuerySet[StockBalance]:
    """Material match: every word in code/name/brand/model/keywords (any order).
    Location match: the whole query in the Location code or name.

    Same Turkish I/i fold as the material catalog search, so "teflon bant",
    "63A" or a keyword alias finds the stock rows the catalog would find.
    """
    normalized = (query or "").strip()
    if not normalized:
        return queryset
    fields = {**_MATERIAL_SEARCH_FIELDS, **_LOCATION_SEARCH_FIELDS}
    queryset = queryset.annotate(
        **{alias: turkish_fold_expr(field) for alias, field in fields.items()}
    )
    material_match = Q()
    for token in normalized.split():
        folded = turkish_casefold(token)
        any_field = Q()
        for alias in _MATERIAL_SEARCH_FIELDS:
            any_field |= Q(**{f"{alias}__contains": folded})
        material_match &= any_field
    whole = turkish_casefold(normalized)
    location_match = Q()
    for alias in _LOCATION_SEARCH_FIELDS:
        location_match |= Q(**{f"{alias}__contains": whole})
    return queryset.filter(material_match | location_match)


def annotate_material_stock_summary(queryset: QuerySet[Material]) -> QuerySet[Material]:
    """Read-only per-material summary for catalog lists.

    Quantity materials: positive on-hand total and the number of stock-holding
    Locations. Serialized materials: assets currently at a Location (IN_STOCK).
    """
    positive = StockBalance.objects.filter(material=OuterRef("pk"), quantity__gt=0).order_by()
    in_stock_assets = SerializedAsset.objects.filter(
        material=OuterRef("pk"), current_location__isnull=False
    ).order_by()
    return queryset.annotate(
        stock_total=Subquery(
            positive.values("material").annotate(total=Sum("quantity")).values("total")[:1]
        ),
        stock_location_count=Subquery(
            positive.values("material")
            .annotate(total=Count("location", distinct=True))
            .values("total")[:1]
        ),
        assets_in_stock=Subquery(
            in_stock_assets.values("material").annotate(total=Count("pk")).values("total")[:1]
        ),
    )


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
