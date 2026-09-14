from __future__ import annotations

import uuid
from collections import defaultdict, deque
from collections.abc import Mapping

from django.core.exceptions import ValidationError

from locations.models import Location


INVALID_SCOPE = "counting.invalid_scope"


def load_location_parent_map(*, using: str = "default") -> dict[uuid.UUID, uuid.UUID | None]:
    """Load the Location tree in one query for deterministic subtree resolution."""
    return dict(
        Location.objects.using(using)
        .order_by("pk")
        .values_list("pk", "parent_id")
    )


def resolve_location_subtree_ids(
    root_location_id,
    *,
    using: str = "default",
    parent_map: Mapping[uuid.UUID, uuid.UUID | None] | None = None,
) -> tuple[uuid.UUID, ...]:
    """Return root + descendants in stable UUID order without recursive queries."""
    root_id = _normalize_location_id(root_location_id)
    tree = dict(parent_map) if parent_map is not None else load_location_parent_map(using=using)
    if root_id not in tree:
        raise ValidationError("Sayım kapsamı bulunamadı.", code=INVALID_SCOPE)

    children: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for location_id, parent_id in tree.items():
        if parent_id is not None:
            children[parent_id].append(location_id)
    for child_ids in children.values():
        child_ids.sort(key=str)

    found: set[uuid.UUID] = set()
    queue: deque[uuid.UUID] = deque([root_id])
    while queue:
        location_id = queue.popleft()
        if location_id in found:
            raise ValidationError(
                "Lokasyon hiyerarşisinde döngü bulundu.",
                code=INVALID_SCOPE,
            )
        found.add(location_id)
        queue.extend(children.get(location_id, ()))
    return tuple(sorted(found, key=str))


def subtrees_overlap(
    first_root_id,
    second_root_id,
    *,
    parent_map: Mapping[uuid.UUID, uuid.UUID | None],
) -> bool:
    first = _normalize_location_id(first_root_id)
    second = _normalize_location_id(second_root_id)
    return _is_ancestor(first, second, parent_map) or _is_ancestor(
        second, first, parent_map
    )


def _is_ancestor(
    possible_ancestor: uuid.UUID,
    location_id: uuid.UUID,
    parent_map: Mapping[uuid.UUID, uuid.UUID | None],
) -> bool:
    current_id: uuid.UUID | None = location_id
    visited: set[uuid.UUID] = set()
    while current_id is not None:
        if current_id == possible_ancestor:
            return True
        if current_id in visited:
            raise ValidationError(
                "Lokasyon hiyerarşisinde döngü bulundu.",
                code=INVALID_SCOPE,
            )
        visited.add(current_id)
        current_id = parent_map.get(current_id)
    return False


def _normalize_location_id(value) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Sayım kapsamı geçersiz.", code=INVALID_SCOPE) from exc
