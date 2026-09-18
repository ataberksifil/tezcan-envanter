from __future__ import annotations

from dataclasses import dataclass

from counting.models import (
    PhysicalCountQuantityLine,
    PhysicalCountSerializedLine,
    PhysicalCountSession,
)
from counting.queries import resolve_location_subtree_ids
from locations.models import Location

VIEW_COUNT_PERMISSION = "counting.view_physicalcountsession"
ADD_COUNT_PERMISSION = "counting.add_physicalcountsession"
CHANGE_COUNT_PERMISSION = "counting.change_physicalcountsession"
DECIDE_COUNT_PERMISSION = "counting.decide_discrepancy"
ESTABLISH_BASELINE_PERMISSION = "imports.establish_baseline"

TERMINAL_QUANTITY_STATUSES = frozenset(
    {
        PhysicalCountQuantityLine.ResolutionStatus.APPROVED,
        PhysicalCountQuantityLine.ResolutionStatus.DISPOSITIONED,
    }
)


@dataclass(frozen=True)
class CountProgress:
    quantity_total: int
    quantity_pending: int
    quantity_not_counted: int
    quantity_counted: int
    quantity_pending_approval: int
    serialized_total: int
    serialized_pending: int
    serialized_not_counted: int
    serialized_observed: int
    serialized_pending_approval: int
    serialized_candidates: int

    @property
    def quantity_remaining(self) -> int:
        return self.quantity_pending

    @property
    def serialized_remaining(self) -> int:
        return self.serialized_pending

    @property
    def blocking_for_completion(self) -> int:
        return self.quantity_pending + self.serialized_pending


def can_see_expected(user) -> bool:
    return bool(user.has_perm(DECIDE_COUNT_PERMISSION))


def session_progress(session: PhysicalCountSession) -> CountProgress:
    quantity_lines = tuple(session.quantity_lines.all())
    serialized_lines = tuple(session.serialized_lines.all())
    q_pending = sum(
        1
        for line in quantity_lines
        if line.resolution_status
        == PhysicalCountQuantityLine.ResolutionStatus.PENDING_COUNT
    )
    q_not = sum(
        1
        for line in quantity_lines
        if line.resolution_status
        == PhysicalCountQuantityLine.ResolutionStatus.NOT_COUNTED
    )
    q_approval = sum(
        1
        for line in quantity_lines
        if line.resolution_status
        == PhysicalCountQuantityLine.ResolutionStatus.PENDING_APPROVAL
    )
    q_counted = sum(
        1
        for line in quantity_lines
        if line.counted_quantity is not None
    )
    s_pending = sum(
        1
        for line in serialized_lines
        if line.resolution_status
        == PhysicalCountSerializedLine.ResolutionStatus.PENDING_COUNT
    )
    s_not = sum(
        1
        for line in serialized_lines
        if line.resolution_status
        == PhysicalCountSerializedLine.ResolutionStatus.NOT_COUNTED
    )
    s_approval = sum(
        1
        for line in serialized_lines
        if line.resolution_status
        == PhysicalCountSerializedLine.ResolutionStatus.PENDING_APPROVAL
    )
    s_observed = sum(1 for line in serialized_lines if line.observed_present is True)
    s_candidates = sum(1 for line in serialized_lines if not line.expected_present)
    return CountProgress(
        quantity_total=len(quantity_lines),
        quantity_pending=q_pending,
        quantity_not_counted=q_not,
        quantity_counted=q_counted,
        quantity_pending_approval=q_approval,
        serialized_total=len(serialized_lines),
        serialized_pending=s_pending,
        serialized_not_counted=s_not,
        serialized_observed=s_observed,
        serialized_pending_approval=s_approval,
        serialized_candidates=s_candidates,
    )


def session_action_flags(user, session: PhysicalCountSession) -> dict[str, bool]:
    draft = session.status == PhysicalCountSession.Status.DRAFT
    started = session.status == PhysicalCountSession.Status.STARTED
    completed = session.status == PhysicalCountSession.Status.COMPLETED
    change = user.has_perm(CHANGE_COUNT_PERMISSION)
    decide = user.has_perm(DECIDE_COUNT_PERMISSION)
    establish = user.has_perm(ESTABLISH_BASELINE_PERMISSION)
    routine_pending = (
        completed
        and not session.baseline_candidate
        and session.reconciliation_status
        == PhysicalCountSession.ReconciliationStatus.PENDING
    )
    return {
        "can_start": draft and change,
        "can_count": started and change,
        "can_complete": started and change,
        "can_review_discrepancies": routine_pending and decide,
        "can_prepare_baseline": (
            completed and session.baseline_candidate and establish
        ),
        "show_expected": decide,
    }


def editable_quantity_lines(session: PhysicalCountSession):
    return session.quantity_lines.select_related(
        "material__unit", "location", "condition", "counted_by_user"
    ).exclude(resolution_status__in=TERMINAL_QUANTITY_STATUSES).order_by(
        "location__code", "material__material_code", "condition__sort_order", "id"
    )


def scope_stock_location_queryset(session: PhysicalCountSession):
    subtree_ids = resolve_location_subtree_ids(session.scope_location_id)
    return Location.objects.filter(
        pk__in=subtree_ids,
        active=True,
        can_hold_stock=True,
    )


def open_sessions_for_warning():
    return (
        PhysicalCountSession.objects.exclude(
            status=PhysicalCountSession.Status.COMPLETED
        )
        .select_related("scope_location")
        .order_by("reference_number", "id")
    )
