from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models.query import QuerySet

from accounts.roles import (
    ADMIN_MANAGER,
    MANAGE_ACCESS_PERMISSION,
    SAFE_CATALOG_PERMISSION_LABELS,
)
from accounts.services.access_management import (
    ROLE_AUDIT_NAMESPACE,
    USER_AUDIT_NAMESPACE,
    assigned_permission_labels,
    canonical_role_snapshot,
    canonical_user_snapshot,
    create_role,
    effective_permission_labels,
    rename_role,
    role_audit_id,
    set_role_permissions,
    set_user_roles,
    user_audit_id,
)
from audit.models import AuditEvent

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "synthetic-test-password-only"


def permission(label: str) -> Permission:
    app_label, codename = label.split(".", 1)
    return Permission.objects.get(
        content_type__app_label=app_label,
        codename=codename,
    )


def grant_group(role: Group, *labels: str) -> None:
    role.permissions.add(*(permission(label) for label in labels))


def make_superuser(username="root"):
    return User.objects.create_superuser(
        username=username, password=PASSWORD, email=""
    )


def make_manager(username="manager", safe_permissions=SAFE_CATALOG_PERMISSION_LABELS):
    actor = User.objects.create_user(username=username, password=PASSWORD)
    role = Group.objects.create(name=f"{username}-access")
    grant_group(role, MANAGE_ACCESS_PERMISSION, *safe_permissions)
    actor.groups.add(role)
    return User.objects.get(pk=actor.pk), role


def make_user(username="target", **kwargs):
    return User.objects.create_user(username=username, password=PASSWORD, **kwargs)


def test_custom_permission_is_exactly_available_and_not_bootstrapped():
    assert permission(MANAGE_ACCESS_PERMISSION).codename == "manage_access"


def test_audit_ids_are_deterministic_namespace_separated_and_rename_stable():
    role = Group.objects.create(name="Stable name")
    user = make_user()
    before = role_audit_id(role)
    role.name = "Changed name"
    role.save(update_fields=["name"])
    assert role_audit_id(role) == before
    assert role_audit_id(role.pk) == before
    assert user_audit_id(role.pk) != role_audit_id(role.pk)
    assert ROLE_AUDIT_NAMESPACE != USER_AUDIT_NAMESPACE
    assert user_audit_id(user) == user_audit_id(user.pk)


def test_create_role_trims_name_and_audits_canonical_snapshot():
    actor = make_superuser()
    result = create_role(actor=actor, name="  Gece Ekibi  ")
    event = AuditEvent.objects.get()
    assert result.changed is True
    assert result.role.name == "Gece Ekibi"
    assert event.event_type == "accounts.role.created"
    assert event.actor == actor
    assert event.entity_id == role_audit_id(result.role)
    assert event.before_data is None
    assert event.after_data == canonical_role_snapshot(result.role)
    assert event.after_data["permissions"] == []


@pytest.mark.parametrize("name", ["", "   ", ADMIN_MANAGER])
def test_create_role_rejects_blank_or_reserved_names(name):
    actor = make_superuser()
    with pytest.raises(ValidationError):
        create_role(actor=actor, name=name)
    assert Group.objects.count() == 0
    assert AuditEvent.objects.count() == 0


def test_create_role_rejects_duplicate_name():
    actor = make_superuser()
    Group.objects.create(name="Duplicate")
    with pytest.raises(ValidationError):
        create_role(actor=actor, name="Duplicate")


def test_database_group_name_uniqueness_is_still_authoritative():
    Group.objects.create(name="DB unique")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Group.objects.create(name="DB unique")


def test_rename_custom_role_audits_full_before_after_and_preserves_id():
    actor = make_superuser()
    role = Group.objects.create(name="Before")
    grant_group(role, "catalog.view_material")
    audit_id = role_audit_id(role)
    result = rename_role(actor=actor, role_id=role.pk, name=" After ")
    event = AuditEvent.objects.get()
    assert result.role.name == "After"
    assert event.event_type == "accounts.role.updated"
    assert event.before_data["name"] == "Before"
    assert event.after_data["name"] == "After"
    assert event.before_data["permissions"] == ["catalog.view_material"]
    assert event.before_data["audit_id"] == str(audit_id)
    assert event.after_data["audit_id"] == str(audit_id)


def test_default_role_and_reserved_target_name_cannot_be_renamed():
    actor = make_superuser()
    default = Group.objects.create(name=ADMIN_MANAGER)
    custom = Group.objects.create(name="Custom")
    with pytest.raises(ValidationError):
        rename_role(actor=actor, role_id=default.pk, name="Other")
    with pytest.raises(ValidationError):
        rename_role(actor=actor, role_id=custom.pk, name=ADMIN_MANAGER)


def test_noop_rename_creates_no_audit_event():
    actor = make_superuser()
    role = Group.objects.create(name="Same")
    result = rename_role(actor=actor, role_id=role.pk, name=" Same ")
    assert result.changed is False
    assert AuditEvent.objects.count() == 0


def test_safe_permissions_and_manage_access_superuser_round_trip_are_audited():
    actor = make_superuser()
    role = Group.objects.create(name="Operators")
    selected = [
        "catalog.view_category",
        "catalog.add_category",
        "catalog.view_material",
    ]
    set_role_permissions(
        actor=actor,
        role_id=role.pk,
        catalog_permissions=selected,
        manage_access=True,
    )
    event = AuditEvent.objects.get()
    assert event.event_type == "accounts.role.permissions_changed"
    assert event.after_data["permissions"] == sorted(
        [*selected, MANAGE_ACCESS_PERMISSION]
    )

    set_role_permissions(
        actor=actor,
        role_id=role.pk,
        catalog_permissions=selected,
        manage_access=False,
    )
    assert AuditEvent.objects.count() == 2
    assert MANAGE_ACCESS_PERMISSION not in canonical_role_snapshot(role)["permissions"]


@pytest.mark.parametrize(
    "forged",
    [
        "catalog.delete_material",
        "auth.change_group",
        "audit.view_auditevent",
        "inventory.view_stockbalance",
        "accounts.manage_access",
    ],
)
def test_service_rejects_every_permission_outside_safe_nine(forged):
    actor = make_superuser()
    role = Group.objects.create(name="Safe")
    with pytest.raises(ValidationError):
        set_role_permissions(
            actor=actor, role_id=role.pk, catalog_permissions=[forged]
        )
    assert role.permissions.count() == 0
    assert AuditEvent.objects.count() == 0


@pytest.mark.parametrize(
    "write_label",
    [
        "catalog.add_category",
        "catalog.change_category",
        "catalog.add_unitofmeasure",
        "catalog.change_unitofmeasure",
        "catalog.add_material",
        "catalog.change_material",
    ],
)
def test_write_permission_without_view_is_rejected(write_label):
    actor = make_superuser()
    role = Group.objects.create(name="Invalid dependency")
    with pytest.raises(ValidationError):
        set_role_permissions(
            actor=actor, role_id=role.pk, catalog_permissions=[write_label]
        )


def test_noop_permission_update_does_not_mutate_or_audit():
    actor = make_superuser()
    role = Group.objects.create(name="No op")
    grant_group(role, "catalog.view_category")
    result = set_role_permissions(
        actor=actor,
        role_id=role.pk,
        catalog_permissions=["catalog.view_category"],
        manage_access=False,
    )
    assert result.changed is False
    assert AuditEvent.objects.count() == 0


def test_unsupported_role_fails_closed_without_stripping_permissions():
    actor = make_superuser()
    role = Group.objects.create(name="Legacy")
    unsupported = permission("catalog.delete_material")
    role.permissions.add(unsupported)
    with pytest.raises(PermissionDenied):
        set_role_permissions(
            actor=actor,
            role_id=role.pk,
            catalog_permissions=["catalog.view_material"],
        )
    with pytest.raises(PermissionDenied):
        rename_role(actor=actor, role_id=role.pk, name="Legacy 2")
    assert set(role.permissions.values_list("pk", flat=True)) == {unsupported.pk}


def test_manager_can_create_role_but_cannot_edit_own_or_manage_access_role():
    actor, own_role = make_manager()
    assert create_role(actor=actor, name="Lower role").changed
    with pytest.raises(PermissionDenied):
        set_role_permissions(actor=actor, role_id=own_role.pk, catalog_permissions=[])
    with pytest.raises(PermissionDenied):
        rename_role(actor=actor, role_id=own_role.pk, name="Renamed own role")
    protected = Group.objects.create(name="Delegated managers")
    grant_group(protected, MANAGE_ACCESS_PERMISSION)
    with pytest.raises(PermissionDenied):
        rename_role(actor=actor, role_id=protected.pk, name="Other managers")


def test_manager_cannot_grant_or_revoke_manage_access():
    actor, _ = make_manager()
    role = Group.objects.create(name="Lower")
    with pytest.raises(PermissionDenied):
        set_role_permissions(
            actor=actor,
            role_id=role.pk,
            catalog_permissions=[],
            manage_access=True,
        )


def test_manager_capability_ceiling_blocks_role_but_allows_equal_role():
    actor, _ = make_manager(
        safe_permissions=("catalog.view_category", "catalog.add_category")
    )
    lower = Group.objects.create(name="Lower")
    set_role_permissions(
        actor=actor,
        role_id=lower.pk,
        catalog_permissions=["catalog.view_category", "catalog.add_category"],
    )
    assert lower.permissions.count() == 2
    with pytest.raises(PermissionDenied):
        set_role_permissions(
            actor=actor,
            role_id=lower.pk,
            catalog_permissions=["catalog.view_material"],
        )


def test_manager_cannot_edit_role_that_already_exceeds_capability():
    actor, _ = make_manager(safe_permissions=("catalog.view_category",))
    role = Group.objects.create(name="Already excessive")
    grant_group(role, "catalog.view_material")
    with pytest.raises(PermissionDenied):
        rename_role(actor=actor, role_id=role.pk, name="Still excessive")
    with pytest.raises(PermissionDenied):
        set_role_permissions(
            actor=actor,
            role_id=role.pk,
            catalog_permissions=["catalog.view_category"],
        )


def test_protected_role_member_blocks_indirect_non_superuser_mutation():
    actor, _ = make_manager()
    role = Group.objects.create(name="Protected members")
    staff = make_user("staff", is_staff=True)
    staff.groups.add(role)
    with pytest.raises(PermissionDenied):
        set_role_permissions(
            actor=actor,
            role_id=role.pk,
            catalog_permissions=["catalog.view_material"],
        )


@pytest.mark.parametrize(
    "member_kind", ["self", "superuser", "direct", "manage", "beyond"]
)
def test_every_other_protected_member_kind_blocks_role_mutation(member_kind):
    actor, _ = make_manager(safe_permissions=("catalog.view_category",))
    role = Group.objects.create(name=f"Protected {member_kind}")
    if member_kind == "self":
        member = actor
    elif member_kind == "superuser":
        member = make_superuser("protected-super")
    else:
        member = make_user(f"protected-{member_kind}")
        if member_kind == "direct":
            member.user_permissions.add(permission("catalog.view_category"))
        elif member_kind == "manage":
            extra = Group.objects.create(name="Protected access")
            grant_group(extra, MANAGE_ACCESS_PERMISSION)
            member.groups.add(extra)
        else:
            extra = Group.objects.create(name="Protected excessive")
            grant_group(extra, "catalog.view_material")
            member.groups.add(extra)
    member.groups.add(role)
    with pytest.raises(PermissionDenied):
        set_role_permissions(
            actor=actor,
            role_id=role.pk,
            catalog_permissions=["catalog.view_category"],
        )


def test_superuser_user_role_assignment_audits_sorted_canonical_state():
    actor = make_superuser()
    target = make_user()
    b = Group.objects.create(name="B")
    a = Group.objects.create(name="A")
    grant_group(a, "catalog.view_material")
    result = set_user_roles(
        actor=actor, user_id=target.pk, role_ids=[b.pk, a.pk]
    )
    event = AuditEvent.objects.get()
    assert result.changed is True
    assert event.event_type == "accounts.user.roles_changed"
    assert event.actor == actor
    assert event.entity_id == user_audit_id(target)
    assert event.after_data == canonical_user_snapshot(target)
    assert event.after_data["roles"] == [
        {"id": b.pk, "name": "B"},
        {"id": a.pk, "name": "A"},
    ] if b.pk < a.pk else [
        {"id": a.pk, "name": "A"},
        {"id": b.pk, "name": "B"},
    ]
    assert event.after_data["effective_permissions"] == ["catalog.view_material"]


def test_user_snapshot_includes_direct_permissions_read_only():
    actor = make_superuser()
    target = make_user()
    direct = permission("catalog.view_category")
    target.user_permissions.add(direct)
    role = Group.objects.create(name="Role")
    set_user_roles(actor=actor, user_id=target.pk, role_ids=[role.pk])
    target.refresh_from_db()
    assert list(target.user_permissions.values_list("pk", flat=True)) == [direct.pk]
    assert canonical_user_snapshot(target)["direct_permissions"] == [
        "catalog.view_category"
    ]


def test_inactive_user_assigned_permissions_remain_visible_but_effective_are_empty():
    target = make_user("inactive-snapshot", is_active=False)
    target.user_permissions.add(permission("catalog.view_category"))
    role = Group.objects.create(name="Inactive assigned")
    grant_group(role, "catalog.view_material")
    target.groups.add(role)

    assert assigned_permission_labels(target) == {
        "catalog.view_category",
        "catalog.view_material",
    }
    assert effective_permission_labels(target) == set()
    assert canonical_user_snapshot(target)["effective_permissions"] == []


def test_noop_user_roles_creates_no_audit():
    actor = make_superuser()
    target = make_user()
    role = Group.objects.create(name="Current")
    target.groups.add(role)
    result = set_user_roles(actor=actor, user_id=target.pk, role_ids=[role.pk])
    assert result.changed is False
    assert AuditEvent.objects.count() == 0


@pytest.mark.parametrize("target_kind", ["self", "staff", "superuser", "direct"])
def test_manager_cannot_modify_protected_target_types(target_kind):
    actor, _ = make_manager()
    if target_kind == "self":
        target = actor
    elif target_kind == "staff":
        target = make_user("staff-target", is_staff=True)
    elif target_kind == "superuser":
        target = make_superuser("super-target")
    else:
        target = make_user("direct-target")
        target.user_permissions.add(permission("catalog.view_category"))
    with pytest.raises(PermissionDenied):
        set_user_roles(actor=actor, user_id=target.pk, role_ids=[])


def test_manager_cannot_assign_or_remove_manage_access_role_or_target():
    actor, _ = make_manager()
    target = make_user()
    access_role = Group.objects.create(name="Access role")
    grant_group(access_role, MANAGE_ACCESS_PERMISSION)
    with pytest.raises(PermissionDenied):
        set_user_roles(actor=actor, user_id=target.pk, role_ids=[access_role.pk])
    target.groups.add(access_role)
    with pytest.raises(PermissionDenied):
        set_user_roles(actor=actor, user_id=target.pk, role_ids=[])


def test_manager_user_capability_ceiling_blocks_excess_and_allows_equal():
    actor, _ = make_manager(
        safe_permissions=("catalog.view_category", "catalog.add_category")
    )
    target = make_user()
    equal = Group.objects.create(name="Equal")
    grant_group(equal, "catalog.view_category", "catalog.add_category")
    excessive = Group.objects.create(name="Excessive")
    grant_group(excessive, "catalog.view_material")
    assert set_user_roles(
        actor=actor, user_id=target.pk, role_ids=[equal.pk]
    ).changed
    with pytest.raises(PermissionDenied):
        set_user_roles(
            actor=actor, user_id=target.pk, role_ids=[equal.pk, excessive.pk]
        )


def test_manager_cannot_modify_target_that_already_exceeds_capability():
    actor, _ = make_manager(safe_permissions=("catalog.view_category",))
    target = make_user()
    excessive = Group.objects.create(name="Current excessive")
    grant_group(excessive, "catalog.view_material")
    target.groups.add(excessive)
    with pytest.raises(PermissionDenied):
        set_user_roles(actor=actor, user_id=target.pk, role_ids=[])


def test_manager_cannot_modify_inactive_target_with_excess_assigned_permissions():
    actor, _ = make_manager(safe_permissions=("catalog.view_category",))
    target = make_user("inactive-excess", is_active=False)
    excessive = Group.objects.create(name="Inactive excessive")
    grant_group(excessive, "catalog.view_material")
    target.groups.add(excessive)

    with pytest.raises(PermissionDenied):
        set_user_roles(actor=actor, user_id=target.pk, role_ids=[])

    assert set(target.groups.values_list("pk", flat=True)) == {excessive.pk}
    assert AuditEvent.objects.count() == 0


def test_manager_cannot_modify_inactive_target_assigned_manage_access():
    actor, _ = make_manager()
    target = make_user("inactive-manager-target", is_active=False)
    access_role = Group.objects.create(name="Inactive delegated access")
    grant_group(access_role, MANAGE_ACCESS_PERMISSION)
    additional_role = Group.objects.create(name="Additional role")
    target.groups.add(access_role)

    with pytest.raises(PermissionDenied):
        set_user_roles(
            actor=actor,
            user_id=target.pk,
            role_ids=[access_role.pk, additional_role.pk],
        )

    assert set(target.groups.values_list("pk", flat=True)) == {access_role.pk}
    assert AuditEvent.objects.count() == 0


@pytest.mark.parametrize(
    ("assigned_label", "role_label"),
    [
        (MANAGE_ACCESS_PERMISSION, None),
        ("catalog.view_material", "catalog.view_category"),
    ],
)
def test_inactive_privileged_member_blocks_role_permission_mutation(
    assigned_label, role_label
):
    actor, _ = make_manager(safe_permissions=("catalog.view_category",))
    role = Group.objects.create(name=f"Inactive protected {assigned_label}")
    if role_label:
        grant_group(role, role_label)
    original_permission_ids = set(role.permissions.values_list("pk", flat=True))
    member = make_user(f"inactive-{assigned_label}", is_active=False)
    extra = Group.objects.create(name=f"Assigned {assigned_label}")
    grant_group(extra, assigned_label)
    member.groups.add(role, extra)

    with pytest.raises(PermissionDenied):
        set_role_permissions(
            actor=actor,
            role_id=role.pk,
            catalog_permissions=[],
        )

    assert set(role.permissions.values_list("pk", flat=True)) == original_permission_ids
    assert AuditEvent.objects.count() == 0


def test_existing_out_of_scope_membership_blocks_even_superuser_assignment():
    actor = make_superuser()
    target = make_user()
    legacy = Group.objects.create(name="Legacy assignment")
    grant_group(legacy, "catalog.delete_category")
    target.groups.add(legacy)
    with pytest.raises(PermissionDenied):
        set_user_roles(actor=actor, user_id=target.pk, role_ids=[])
    assert target.groups.filter(pk=legacy.pk).exists()


def test_non_superuser_cannot_assign_or_remove_out_of_scope_role():
    actor, _ = make_manager()
    target = make_user()
    legacy = Group.objects.create(name="Non-super legacy")
    grant_group(legacy, "catalog.delete_category")
    with pytest.raises(PermissionDenied):
        set_user_roles(actor=actor, user_id=target.pk, role_ids=[legacy.pk])
    target.groups.add(legacy)
    with pytest.raises(PermissionDenied):
        set_user_roles(actor=actor, user_id=target.pk, role_ids=[])


def test_superuser_can_assign_and_remove_supported_manage_access_role():
    actor = make_superuser()
    target = make_user()
    role = Group.objects.create(name="Delegated")
    grant_group(role, MANAGE_ACCESS_PERMISSION)
    assert set_user_roles(actor=actor, user_id=target.pk, role_ids=[role.pk]).changed
    assert set_user_roles(actor=actor, user_id=target.pk, role_ids=[]).changed


def test_persisted_same_database_actor_is_required():
    unsaved = User(username="unsaved", is_superuser=True)
    with pytest.raises(ValidationError):
        create_role(actor=unsaved, name="Denied")
    actor = make_superuser()
    actor._state.db = "other"
    with pytest.raises(ValidationError):
        create_role(actor=actor, name="Wrong database")


def test_role_and_user_mutations_execute_select_for_update_paths(monkeypatch):
    actor = make_superuser()
    role = Group.objects.create(name="Locked role")
    target = make_user()
    locked_models = []
    original = QuerySet.select_for_update

    def recording_select_for_update(queryset, *args, **kwargs):
        locked_models.append(queryset.model)
        return original(queryset, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "select_for_update", recording_select_for_update)
    set_role_permissions(
        actor=actor,
        role_id=role.pk,
        catalog_permissions=["catalog.view_material"],
    )
    set_user_roles(actor=actor, user_id=target.pk, role_ids=[role.pk])
    assert locked_models.count(Group) >= 2
    assert User in locked_models


def test_service_authorization_does_not_accept_name_or_staff_as_substitute():
    for username, kwargs in (
        ("named", {}),
        ("staff-only", {"is_staff": True}),
    ):
        actor = make_user(username, **kwargs)
        if username == "named":
            role = Group.objects.create(name=ADMIN_MANAGER)
            actor.groups.add(role)
        with pytest.raises(PermissionDenied):
            create_role(actor=actor, name=f"Denied {username}")


def test_service_rechecks_manage_access_from_database_not_actor_permission_cache():
    actor, access_role = make_manager()
    assert actor.has_perm(MANAGE_ACCESS_PERMISSION) is True
    access_role.permissions.remove(permission(MANAGE_ACCESS_PERMISSION))
    with pytest.raises(PermissionDenied):
        create_role(actor=actor, name="Stale cache must not authorize")


def test_inactive_actor_with_assigned_manage_access_cannot_mutate_access():
    actor, _ = make_manager("inactive-actor")
    actor.is_active = False
    actor.save(update_fields=["is_active"])

    with pytest.raises(PermissionDenied):
        create_role(actor=actor, name="Inactive actor denied")

    assert not Group.objects.filter(name="Inactive actor denied").exists()
    assert AuditEvent.objects.count() == 0


@pytest.mark.parametrize("operation", ["create", "rename", "permissions", "roles"])
def test_audit_failure_rolls_back_every_mutation(operation):
    actor = make_superuser()
    role = Group.objects.create(name="Original")
    target = make_user()
    before_group_count = Group.objects.count()
    with patch(
        "accounts.services.access_management.record_audit_event",
        side_effect=RuntimeError("simulated audit failure"),
    ):
        with pytest.raises(RuntimeError):
            if operation == "create":
                create_role(actor=actor, name="Rolled back")
            elif operation == "rename":
                rename_role(actor=actor, role_id=role.pk, name="Changed")
            elif operation == "permissions":
                set_role_permissions(
                    actor=actor,
                    role_id=role.pk,
                    catalog_permissions=["catalog.view_material"],
                )
            else:
                set_user_roles(actor=actor, user_id=target.pk, role_ids=[role.pk])
    role.refresh_from_db()
    target.refresh_from_db()
    assert Group.objects.count() == before_group_count
    assert role.name == "Original"
    assert role.permissions.count() == 0
    assert target.groups.count() == 0
    assert AuditEvent.objects.count() == 0
