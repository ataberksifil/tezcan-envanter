from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import connection
from django.urls import reverse

from accounts.roles import ADMIN_MANAGER, TECHNICIAN
from audit.models import AuditEvent
from catalog.models import Category, Material, UnitOfMeasure
from catalog.services.categories import set_category_active
from catalog.services.materials import (
    _serialize_minimum_stock_value,
    canonical_material_snapshot,
    create_material,
    set_material_active,
    update_material,
)
from catalog.services.units_of_measure import set_unit_of_measure_active

pytestmark = pytest.mark.django_db

PASSWORD = "synthetic-test-password-only"
CANONICAL_FIELDS = {
    "id",
    "material_code",
    "name",
    "category_id",
    "brand",
    "model",
    "unit_id",
    "tracking_mode",
    "minimum_stock_value",
    "technical_specs",
    "active",
}
FORBIDDEN_SNAPSHOT_KEYS = {
    "created_at",
    "updated_at",
    "username",
    "session",
    "csrf",
    "ip",
    "device",
    "request",
}


def _catalog_and_audit_tables_exist() -> bool:
    table_names = connection.introspection.table_names()
    return "catalog_material" in table_names and "audit_auditevent" in table_names


@pytest.fixture(autouse=True)
def require_catalog_and_audit_schema():
    if not _catalog_and_audit_tables_exist():
        pytest.skip(
            "catalog/audit migrations not applied to test_tezcan_envanter; "
            "material audit tests deferred until test DB is migrated"
        )


@pytest.fixture
def app_client(client):
    client.defaults["HTTP_HOST"] = "localhost"
    return client


def _refresh_user_permissions(user):
    user = get_user_model().objects.get(pk=user.pk)
    for cache_attr in ("_perm_cache", "_group_perm_cache", "_user_perm_cache"):
        if hasattr(user, cache_attr):
            delattr(user, cache_attr)
    return user


def _create_ordinary_user(username):
    user_model = get_user_model()
    user = user_model.objects.create_user(username=username, password=PASSWORD)
    assert not user.is_superuser
    assert not user.is_staff
    return user


def _role_user(role_name, username=None):
    call_command("setup_roles", verbosity=0)
    user = _create_ordinary_user(username or f"{role_name.lower()}-{uuid.uuid4().hex[:8]}")
    user.groups.add(Group.objects.get(name=role_name))
    user = _refresh_user_permissions(user)
    assert not user.is_superuser
    return user


def _login(app_client, user):
    app_client.force_login(user)
    return user


def _admin_actor():
    return _role_user(ADMIN_MANAGER, f"mat-audit-admin-{uuid.uuid4().hex[:8]}")


def _unauthorized_actor():
    return _create_ordinary_user(f"mat-audit-none-{uuid.uuid4().hex[:8]}")


def _category(*, active=True):
    return Category.objects.create(name=f"Cat-{uuid.uuid4().hex[:6]}", active=active)


def _unit(*, active=True):
    return UnitOfMeasure.objects.create(
        code=f"U-{uuid.uuid4().hex[:6]}",
        name="Unit",
        active=active,
    )


def _material(**overrides):
    category = overrides.pop("category", None) or _category()
    explicit_unit = "unit" in overrides
    unit = overrides.pop("unit", None)
    tracking_mode = overrides.get("tracking_mode", Material.TrackingMode.QUANTITY)
    if not explicit_unit:
        unit = _unit() if tracking_mode == Material.TrackingMode.QUANTITY else None
    defaults = {
        "material_code": f"MAT-{uuid.uuid4().hex[:6]}",
        "name": f"Material {uuid.uuid4().hex[:6]}",
        "category": category,
        "tracking_mode": tracking_mode,
        "unit": unit,
    }
    defaults.update(overrides)
    return Material.objects.create(**defaults)


def _events_for(entity_id):
    return AuditEvent.objects.filter(
        entity_type="catalog.material",
        entity_id=entity_id,
    ).order_by("occurred_at", "id")


def _assert_canonical_snapshot(payload, material):
    assert payload is not None
    assert set(payload.keys()) == CANONICAL_FIELDS
    for key in FORBIDDEN_SNAPSHOT_KEYS:
        assert key not in payload
    assert payload == canonical_material_snapshot(material)


def _create_payload(**overrides):
    category = overrides.pop("category", None) or _category()
    unit = overrides.pop("unit", None)
    if unit is None and overrides.get("tracking_mode", Material.TrackingMode.QUANTITY) == Material.TrackingMode.QUANTITY:
        unit = _unit()
    defaults = {
        "actor": _admin_actor(),
        "material_code": f"CODE-{uuid.uuid4().hex[:6]}",
        "name": f"Name-{uuid.uuid4().hex[:6]}",
        "category_id": category.pk,
        "brand": None,
        "model": None,
        "unit_id": unit.pk if unit is not None else None,
        "tracking_mode": Material.TrackingMode.QUANTITY,
        "minimum_stock_value": None,
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Admin bypass
# ---------------------------------------------------------------------------


def test_material_not_registered_in_admin():
    assert Material not in admin.site._registry


# ---------------------------------------------------------------------------
# DB alias
# ---------------------------------------------------------------------------


def test_create_material_full_clean_sees_requested_db_alias():
    actor = _admin_actor()
    category = _category()
    unit = _unit()
    using = "default"
    seen_db: list[str | None] = []
    original_full_clean = Material.full_clean

    def tracking_full_clean(self, *args, **kwargs):
        seen_db.append(self._state.db)
        assert self._state.db == using
        return original_full_clean(self, *args, **kwargs)

    with patch.object(Material, "full_clean", tracking_full_clean):
        create_material(
            actor=actor,
            material_code="ALIAS-1",
            name="Alias Material",
            category_id=category.pk,
            unit_id=unit.pk,
            tracking_mode=Material.TrackingMode.QUANTITY,
            using=using,
        )

    assert seen_db == [using]


def test_actor_database_alias_mismatch_rejected():
    actor = _admin_actor()
    category = _category()
    unit = _unit()
    actor._state.db = "other"
    with patch.object(actor, "has_perm", return_value=True):
        with pytest.raises(ValidationError):
            create_material(
                actor=actor,
                material_code="MISMATCH",
                name="Mismatch",
                category_id=category.pk,
                unit_id=unit.pk,
                tracking_mode=Material.TrackingMode.QUANTITY,
                using="default",
            )


def test_update_and_status_use_selected_db_alias():
    actor = _admin_actor()
    material = _material()
    using = "default"
    seen_aliases: list[str] = []
    original_locked = Material.objects.using(using).select_for_update().get

    def tracking_locked(material_id, alias):
        assert alias == using
        seen_aliases.append(alias)
        return original_locked(pk=material_id)

    with patch(
        "catalog.services.materials._locked_material",
        side_effect=tracking_locked,
    ):
        update_material(
            actor=actor,
            material_id=material.pk,
            material_code=material.material_code,
            name="Alias Update",
            category_id=material.category_id,
            unit_id=material.unit_id,
            tracking_mode=material.tracking_mode,
            using=using,
        )
        set_material_active(
            actor=actor,
            material_id=material.pk,
            active=False,
            using=using,
        )

    assert seen_aliases == [using, using]


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------


def test_successful_create_produces_exactly_one_canonical_event():
    actor = _admin_actor()
    category = _category()
    unit = _unit()
    before_events = AuditEvent.objects.count()

    result = create_material(
        actor=actor,
        material_code="CREATE-CODE",
        name="Create Material",
        category_id=category.pk,
        unit_id=unit.pk,
        tracking_mode=Material.TrackingMode.QUANTITY,
        minimum_stock_value=Decimal("5.000"),
    )
    material = result.material
    events = list(_events_for(material.id))

    assert result.changed is True
    assert material.technical_specs == {}
    assert material.active is True
    assert AuditEvent.objects.count() == before_events + 1
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "catalog.material.created"
    assert event.actor_id == actor.pk
    assert event.entity_type == "catalog.material"
    assert event.entity_id == material.id
    assert event.before_data is None
    _assert_canonical_snapshot(event.after_data, material)
    assert event.after_data["minimum_stock_value"] == "5.000"


def test_create_duplicate_material_code_succeeds():
    actor = _admin_actor()
    category = _category()
    unit = _unit()
    code = f"DUP-{uuid.uuid4().hex[:6]}"
    first = create_material(
        actor=actor,
        material_code=code,
        name="First",
        category_id=category.pk,
        unit_id=unit.pk,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    second = create_material(
        actor=actor,
        material_code=code,
        name="Second",
        category_id=category.pk,
        unit_id=unit.pk,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    assert first.material.pk != second.material.pk
    assert Material.objects.filter(material_code=code).count() == 2


# ---------------------------------------------------------------------------
# UPDATE / NO-OP
# ---------------------------------------------------------------------------


def test_successful_update_produces_before_after_event():
    actor = _admin_actor()
    material = _material(
        material_code="BEFORE",
        name="Before Name",
        brand="OldBrand",
        minimum_stock_value=Decimal("1.000"),
        technical_specs={"voltage": "230V"},
    )
    before_events = AuditEvent.objects.count()
    before = canonical_material_snapshot(material)

    result = update_material(
        actor=actor,
        material_id=material.pk,
        material_code="AFTER",
        name="After Name",
        category_id=material.category_id,
        brand="NewBrand",
        model="NewModel",
        unit_id=material.unit_id,
        tracking_mode=Material.TrackingMode.QUANTITY,
        minimum_stock_value=Decimal("2.500"),
    )
    material.refresh_from_db()
    events = list(_events_for(material.id))

    assert result.changed is True
    assert material.technical_specs == {"voltage": "230V"}
    assert material.active is True
    assert AuditEvent.objects.count() == before_events + 1
    event = events[0]
    assert event.event_type == "catalog.material.updated"
    assert event.before_data == before
    _assert_canonical_snapshot(event.after_data, material)
    assert event.after_data["material_code"] == "AFTER"
    assert event.after_data["minimum_stock_value"] == "2.500"


def test_noop_update_does_not_save_or_audit():
    actor = _admin_actor()
    material = _material(
        material_code="NOOP",
        name="Noop Name",
        brand="Brand",
        technical_specs={"keep": True},
        active=False,
    )
    original_updated_at = material.updated_at
    before_events = AuditEvent.objects.count()

    result = update_material(
        actor=actor,
        material_id=material.pk,
        material_code=" NOOP ",
        name=" Noop Name ",
        category_id=material.category_id,
        brand=" Brand ",
        model=None,
        unit_id=material.unit_id,
        tracking_mode=material.tracking_mode,
        minimum_stock_value=material.minimum_stock_value,
    )
    material.refresh_from_db()

    assert result.changed is False
    assert material.updated_at == original_updated_at
    assert material.active is False
    assert material.technical_specs == {"keep": True}
    assert AuditEvent.objects.count() == before_events


def test_repeated_deactivate_and_reactivate_are_noops():
    actor = _admin_actor()
    material = _material(active=True)
    first = set_material_active(actor=actor, material_id=material.pk, active=False)
    material.refresh_from_db()
    updated_at = material.updated_at
    event_count = AuditEvent.objects.count()

    second = set_material_active(actor=actor, material_id=material.pk, active=False)
    material.refresh_from_db()
    assert first.changed is True
    assert second.changed is False
    assert material.updated_at == updated_at
    assert AuditEvent.objects.count() == event_count

    third = set_material_active(actor=actor, material_id=material.pk, active=True)
    material.refresh_from_db()
    reactivated_at = material.updated_at
    event_count = AuditEvent.objects.count()
    fourth = set_material_active(actor=actor, material_id=material.pk, active=True)
    material.refresh_from_db()
    assert third.changed is True
    assert fourth.changed is False
    assert material.updated_at == reactivated_at
    assert AuditEvent.objects.count() == event_count


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_create_normalizes_trimmed_fields():
    actor = _admin_actor()
    category = _category()
    unit = _unit()
    result = create_material(
        actor=actor,
        material_code="  CODE-TRIM  ",
        name="  Name Trim  ",
        category_id=category.pk,
        brand="  ",
        model="  ",
        unit_id=unit.pk,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    material = result.material
    assert material.material_code == "CODE-TRIM"
    assert material.name == "Name Trim"
    assert material.brand is None
    assert material.model is None


def test_create_rejects_blank_code_or_name():
    actor = _admin_actor()
    category = _category()
    unit = _unit()
    with pytest.raises(ValidationError):
        create_material(
            actor=actor,
            material_code="   ",
            name="Valid",
            category_id=category.pk,
            unit_id=unit.pk,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
    with pytest.raises(ValidationError):
        create_material(
            actor=actor,
            material_code="Valid",
            name="   ",
            category_id=category.pk,
            unit_id=unit.pk,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )


def test_unicode_and_internal_whitespace_preserved():
    actor = _admin_actor()
    category = _category()
    unit = _unit()
    code = "MaT-İç  Boşluk"
    name = "Ürün  Adı"
    result = create_material(
        actor=actor,
        material_code=code,
        name=name,
        category_id=category.pk,
        brand="Marka  X",
        model="Model  Y",
        unit_id=unit.pk,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    material = result.material
    assert material.material_code == code
    assert material.name == name
    assert material.brand == "Marka  X"
    assert material.model == "Model  Y"


# ---------------------------------------------------------------------------
# Tracking mode / minimum stock
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tracking_mode", "unit", "should_fail"),
    [
        (Material.TrackingMode.QUANTITY, "present", False),
        (Material.TrackingMode.QUANTITY, None, True),
        (Material.TrackingMode.SERIALIZED, "present", False),
        (Material.TrackingMode.SERIALIZED, None, False),
    ],
)
def test_tracking_mode_unit_rules(tracking_mode, unit, should_fail):
    actor = _admin_actor()
    category = _category()
    unit_obj = _unit() if unit == "present" else None
    kwargs = _create_payload(
        actor=actor,
        category_id=category.pk,
        tracking_mode=tracking_mode,
        unit_id=unit_obj.pk if unit_obj is not None else None,
    )
    if should_fail:
        with pytest.raises(ValidationError):
            create_material(**kwargs)
    else:
        result = create_material(**kwargs)
        assert result.changed is True


def test_tracking_mode_change_allowed_without_inventory_history():
    actor = _admin_actor()
    material = _material(tracking_mode=Material.TrackingMode.QUANTITY)
    result = update_material(
        actor=actor,
        material_id=material.pk,
        material_code=material.material_code,
        name=material.name,
        category_id=material.category_id,
        unit_id=None,
        tracking_mode=Material.TrackingMode.SERIALIZED,
        minimum_stock_value=material.minimum_stock_value,
    )
    material.refresh_from_db()
    assert result.changed is True
    assert material.tracking_mode == Material.TrackingMode.SERIALIZED
    assert material.unit_id is None

    with pytest.raises(ValidationError):
        update_material(
            actor=actor,
            material_id=material.pk,
            material_code=material.material_code,
            name=material.name,
            category_id=material.category_id,
            unit_id=None,
            tracking_mode=Material.TrackingMode.QUANTITY,
            minimum_stock_value=material.minimum_stock_value,
        )
    unit = _unit()
    result = update_material(
        actor=actor,
        material_id=material.pk,
        material_code=material.material_code,
        name=material.name,
        category_id=material.category_id,
        unit_id=unit.pk,
        tracking_mode=Material.TrackingMode.QUANTITY,
        minimum_stock_value=material.minimum_stock_value,
    )
    material.refresh_from_db()
    assert result.changed is True
    assert material.unit_id == unit.pk


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        (None, True),
        (Decimal("0"), True),
        (Decimal("12.345"), True),
        (Decimal("-0.001"), False),
        (Decimal("0.0001"), False),
    ],
)
def test_minimum_stock_value_rules(value, valid):
    actor = _admin_actor()
    category = _category()
    unit = _unit()
    kwargs = _create_payload(
        actor=actor,
        category_id=category.pk,
        unit_id=unit.pk,
        minimum_stock_value=value,
    )
    if valid:
        result = create_material(**kwargs)
        material = result.material
        assert material.minimum_stock_value == value
        if value is not None:
            event = _events_for(material.id).get()
            assert event.after_data["minimum_stock_value"] == _serialize_minimum_stock_value(
                value
            )
    else:
        with pytest.raises(ValidationError):
            create_material(**kwargs)


@pytest.mark.parametrize("incoming", [Decimal("1"), Decimal("1.0")])
def test_update_with_equivalent_minimum_stock_decimal_scale_is_noop(incoming):
    actor = _admin_actor()
    material = _material(minimum_stock_value=Decimal("1.000"))
    original_updated_at = material.updated_at
    before_events = AuditEvent.objects.count()

    result = update_material(
        actor=actor,
        material_id=material.pk,
        material_code=material.material_code,
        name=material.name,
        category_id=material.category_id,
        unit_id=material.unit_id,
        tracking_mode=material.tracking_mode,
        minimum_stock_value=incoming,
    )
    material.refresh_from_db()

    assert result.changed is False
    assert material.minimum_stock_value == Decimal("1.000")
    assert material.updated_at == original_updated_at
    assert AuditEvent.objects.count() == before_events
    assert _events_for(material.id).count() == 0


def test_update_with_real_minimum_stock_change_audits_canonical_decimals():
    actor = _admin_actor()
    material = _material(minimum_stock_value=Decimal("1.000"))
    original_updated_at = material.updated_at
    before_events = AuditEvent.objects.count()

    result = update_material(
        actor=actor,
        material_id=material.pk,
        material_code=material.material_code,
        name=material.name,
        category_id=material.category_id,
        unit_id=material.unit_id,
        tracking_mode=material.tracking_mode,
        minimum_stock_value=Decimal("2"),
    )
    material.refresh_from_db()
    events = list(_events_for(material.id))

    assert result.changed is True
    assert material.minimum_stock_value == Decimal("2.000")
    assert material.updated_at > original_updated_at
    assert AuditEvent.objects.count() == before_events + 1
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "catalog.material.updated"
    assert event.before_data["minimum_stock_value"] == "1.000"
    assert event.after_data["minimum_stock_value"] == "2.000"


def test_create_audit_snapshot_uses_canonical_minimum_stock_decimal():
    actor = _admin_actor()
    category = _category()
    unit = _unit()
    result = create_material(
        actor=actor,
        material_code="DEC-SCALE",
        name="Decimal Scale",
        category_id=category.pk,
        unit_id=unit.pk,
        tracking_mode=Material.TrackingMode.QUANTITY,
        minimum_stock_value=Decimal("1"),
    )
    event = _events_for(result.material.id).get()
    assert event.after_data["minimum_stock_value"] == "1.000"


# ---------------------------------------------------------------------------
# Inactive references
# ---------------------------------------------------------------------------


def test_create_with_inactive_category_rejected():
    actor = _admin_actor()
    inactive = _category(active=False)
    unit = _unit()
    with pytest.raises(ValidationError) as exc_info:
        create_material(
            actor=actor,
            material_code="INACT-CAT",
            name="Inactive Cat",
            category_id=inactive.pk,
            unit_id=unit.pk,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
    assert "category" in exc_info.value.error_dict


def test_create_with_inactive_unit_rejected():
    actor = _admin_actor()
    category = _category()
    inactive = _unit(active=False)
    with pytest.raises(ValidationError) as exc_info:
        create_material(
            actor=actor,
            material_code="INACT-UOM",
            name="Inactive Uom",
            category_id=category.pk,
            unit_id=inactive.pk,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
    assert "unit" in exc_info.value.error_dict


def test_update_rejects_different_inactive_category():
    actor = _admin_actor()
    inactive = _category(active=False)
    material = _material(category=inactive)
    other_inactive = _category(active=False)
    with pytest.raises(ValidationError):
        update_material(
            actor=actor,
            material_id=material.pk,
            material_code=material.material_code,
            name=material.name,
            category_id=other_inactive.pk,
            unit_id=material.unit_id,
            tracking_mode=material.tracking_mode,
        )


def test_update_preserves_existing_inactive_category_while_editing_other_fields():
    actor = _admin_actor()
    inactive = _category(active=False)
    material = _material(category=inactive, name="Keep Cat")
    result = update_material(
        actor=actor,
        material_id=material.pk,
        material_code="NEW-CODE",
        name="New Name",
        category_id=inactive.pk,
        unit_id=material.unit_id,
        tracking_mode=material.tracking_mode,
    )
    material.refresh_from_db()
    assert result.changed is True
    assert material.category_id == inactive.pk
    assert material.name == "New Name"


def test_deactivating_referenced_category_does_not_mutate_material():
    actor = _admin_actor()
    category = _category(active=True)
    material = _material(category=category)
    material_updated_at = material.updated_at
    set_category_active(actor=actor, category_id=category.pk, active=False)
    material.refresh_from_db()
    category.refresh_from_db()
    assert category.active is False
    assert material.category_id == category.pk
    assert material.updated_at == material_updated_at


def test_update_rejects_different_inactive_unit():
    actor = _admin_actor()
    inactive = _unit(active=False)
    material = _material(unit=inactive)
    other_inactive = _unit(active=False)
    with pytest.raises(ValidationError):
        update_material(
            actor=actor,
            material_id=material.pk,
            material_code=material.material_code,
            name=material.name,
            category_id=material.category_id,
            unit_id=other_inactive.pk,
            tracking_mode=material.tracking_mode,
        )


def test_update_preserves_existing_inactive_unit_while_editing_other_fields():
    actor = _admin_actor()
    inactive = _unit(active=False)
    material = _material(unit=inactive, name="Keep Unit")
    result = update_material(
        actor=actor,
        material_id=material.pk,
        material_code="UNIT-CODE",
        name="Updated Name",
        category_id=material.category_id,
        unit_id=inactive.pk,
        tracking_mode=material.tracking_mode,
    )
    material.refresh_from_db()
    assert result.changed is True
    assert material.unit_id == inactive.pk


def test_deactivating_referenced_unit_does_not_mutate_material():
    actor = _admin_actor()
    unit = _unit(active=True)
    material = _material(unit=unit)
    material_updated_at = material.updated_at
    set_unit_of_measure_active(actor=actor, unit_of_measure_id=unit.pk, active=False)
    material.refresh_from_db()
    unit.refresh_from_db()
    assert unit.active is False
    assert material.unit_id == unit.pk
    assert material.updated_at == material_updated_at


# ---------------------------------------------------------------------------
# Technical specs preservation
# ---------------------------------------------------------------------------


def test_update_preserves_existing_technical_specs():
    actor = _admin_actor()
    specs = {"nested": {"a": 1}, "list": [1, 2]}
    material = _material(technical_specs=specs)
    update_material(
        actor=actor,
        material_id=material.pk,
        material_code="SPEC-KEEP",
        name=material.name,
        category_id=material.category_id,
        unit_id=material.unit_id,
        tracking_mode=material.tracking_mode,
    )
    material.refresh_from_db()
    assert material.technical_specs == specs
    event = _events_for(material.id).get()
    assert event.after_data["technical_specs"] == specs


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_unauthorized_create_has_no_material_or_audit():
    actor = _unauthorized_actor()
    category = _category()
    unit = _unit()
    before_ids = set(Material.objects.values_list("pk", flat=True))
    before_events = AuditEvent.objects.count()
    with pytest.raises(PermissionDenied):
        create_material(
            actor=actor,
            material_code="NOPE",
            name="Unauthorized",
            category_id=category.pk,
            unit_id=unit.pk,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
    assert set(Material.objects.values_list("pk", flat=True)) == before_ids
    assert AuditEvent.objects.count() == before_events


def test_unauthorized_update_and_status_have_no_audit():
    actor = _unauthorized_actor()
    material = _material(name="Protected")
    updated_at = material.updated_at
    before_events = AuditEvent.objects.count()
    with pytest.raises(PermissionDenied):
        update_material(
            actor=actor,
            material_id=material.pk,
            material_code="HACK",
            name="Hacked",
            category_id=material.category_id,
            unit_id=material.unit_id,
            tracking_mode=material.tracking_mode,
        )
    with pytest.raises(PermissionDenied):
        set_material_active(actor=actor, material_id=material.pk, active=False)
    material.refresh_from_db()
    assert material.name == "Protected"
    assert material.active is True
    assert material.updated_at == updated_at
    assert AuditEvent.objects.count() == before_events


def test_technician_service_calls_are_unauthorized():
    actor = _role_user(TECHNICIAN, "tech-mat-denied")
    material = _material(name="Tech Target")
    category = _category()
    unit = _unit()
    before_events = AuditEvent.objects.count()
    with pytest.raises(PermissionDenied):
        create_material(
            actor=actor,
            material_code="TECH",
            name="Tech Create",
            category_id=category.pk,
            unit_id=unit.pk,
            tracking_mode=Material.TrackingMode.QUANTITY,
        )
    with pytest.raises(PermissionDenied):
        update_material(
            actor=actor,
            material_id=material.pk,
            material_code=material.material_code,
            name="Tech Edit",
            category_id=material.category_id,
            unit_id=material.unit_id,
            tracking_mode=material.tracking_mode,
        )
    with pytest.raises(PermissionDenied):
        set_material_active(actor=actor, material_id=material.pk, active=False)
    assert AuditEvent.objects.count() == before_events


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


class SimulatedAuditAppendFailure(Exception):
    pass


def _failing_audit_append(*args, **kwargs):
    raise SimulatedAuditAppendFailure("simulated audit append failure")


@pytest.mark.django_db(transaction=True)
def test_create_rolls_back_when_audit_append_fails():
    actor = _admin_actor()
    category = _category()
    unit = _unit()
    before_ids = set(Material.objects.values_list("pk", flat=True))
    before_events = AuditEvent.objects.count()
    with patch(
        "catalog.services.materials.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            create_material(
                actor=actor,
                material_code="RB-CREATE",
                name="Rollback Create",
                category_id=category.pk,
                unit_id=unit.pk,
                tracking_mode=Material.TrackingMode.QUANTITY,
            )
    assert set(Material.objects.values_list("pk", flat=True)) == before_ids
    assert AuditEvent.objects.count() == before_events


@pytest.mark.django_db(transaction=True)
def test_update_rolls_back_when_audit_append_fails():
    actor = _admin_actor()
    material = _material(name="Rollback Old")
    original_updated_at = material.updated_at
    before_events = AuditEvent.objects.count()
    with patch(
        "catalog.services.materials.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            update_material(
                actor=actor,
                material_id=material.pk,
                material_code=material.material_code,
                name="Rollback New",
                category_id=material.category_id,
                unit_id=material.unit_id,
                tracking_mode=material.tracking_mode,
            )
    material.refresh_from_db()
    assert material.name == "Rollback Old"
    assert material.updated_at == original_updated_at
    assert AuditEvent.objects.count() == before_events


@pytest.mark.django_db(transaction=True)
def test_deactivate_rolls_back_when_audit_append_fails():
    actor = _admin_actor()
    material = _material(active=True)
    original_updated_at = material.updated_at
    before_events = AuditEvent.objects.count()
    with patch(
        "catalog.services.materials.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            set_material_active(actor=actor, material_id=material.pk, active=False)
    material.refresh_from_db()
    assert material.active is True
    assert material.updated_at == original_updated_at
    assert AuditEvent.objects.count() == before_events


@pytest.mark.django_db(transaction=True)
def test_reactivate_rolls_back_when_audit_append_fails():
    actor = _admin_actor()
    material = _material(active=False)
    original_updated_at = material.updated_at
    before_events = AuditEvent.objects.count()
    with patch(
        "catalog.services.materials.record_audit_event",
        side_effect=_failing_audit_append,
    ):
        with pytest.raises(SimulatedAuditAppendFailure):
            set_material_active(actor=actor, material_id=material.pk, active=True)
    material.refresh_from_db()
    assert material.active is False
    assert material.updated_at == original_updated_at
    assert AuditEvent.objects.count() == before_events


# ---------------------------------------------------------------------------
# HTTP integration
# ---------------------------------------------------------------------------


def _valid_material_post(category, unit, **overrides):
    data = {
        "material_code": overrides.get("material_code", f"HTTP-{uuid.uuid4().hex[:6]}"),
        "name": overrides.get("name", "HTTP Material"),
        "category": str(category.pk),
        "brand": overrides.get("brand", ""),
        "model": overrides.get("model", ""),
        "unit": str(unit.pk) if unit is not None else "",
        "tracking_mode": overrides.get("tracking_mode", Material.TrackingMode.QUANTITY),
        "minimum_stock_value": overrides.get("minimum_stock_value", ""),
    }
    data.update(overrides)
    return data


def test_http_create_without_add_permission_returns_403(app_client):
    category = _category()
    unit = _unit()
    material = _material()
    user = _role_user(TECHNICIAN, "http-mat-create-denied")
    _login(app_client, user)
    before_events = AuditEvent.objects.count()
    before_count = Material.objects.count()
    response = app_client.post(
        reverse("catalog:material-create"),
        _valid_material_post(category, unit, name="Denied Create"),
    )
    assert response.status_code == 403
    assert Material.objects.count() == before_count
    assert AuditEvent.objects.count() == before_events
    assert Material.objects.filter(name="Denied Create").count() == 0


def test_http_update_without_change_permission_returns_403(app_client):
    material = _material(name="Denied Update Target")
    user = _role_user(TECHNICIAN, "http-mat-update-denied")
    _login(app_client, user)
    before_events = AuditEvent.objects.count()
    response = app_client.post(
        reverse("catalog:material-update", args=[material.pk]),
        _valid_material_post(
            material.category,
            material.unit,
            material_code=material.material_code,
            name="Denied Edit",
        ),
    )
    assert response.status_code == 403
    material.refresh_from_db()
    assert material.name == "Denied Update Target"
    assert AuditEvent.objects.count() == before_events


def test_http_status_without_change_permission_returns_403(app_client):
    material = _material(active=True)
    user = _role_user(TECHNICIAN, "http-mat-status-denied")
    _login(app_client, user)
    before_events = AuditEvent.objects.count()
    response = app_client.post(reverse("catalog:material-deactivate", args=[material.pk]))
    assert response.status_code == 403
    material.refresh_from_db()
    assert material.active is True
    assert AuditEvent.objects.count() == before_events


def test_http_create_and_update_work(app_client):
    user = _admin_actor()
    category = _category()
    unit = _unit()
    _login(app_client, user)
    create = app_client.post(
        reverse("catalog:material-create"),
        _valid_material_post(category, unit, name="HTTP Created"),
        follow=True,
    )
    assert create.status_code == 200
    assert "Malzeme oluşturuldu." in create.content.decode()
    material = Material.objects.get(name="HTTP Created")
    update = app_client.post(
        reverse("catalog:material-update", args=[material.pk]),
        _valid_material_post(
            category,
            unit,
            material_code=material.material_code,
            name="HTTP Updated",
        ),
        follow=True,
    )
    assert update.status_code == 200
    assert "Malzeme güncellendi." in update.content.decode()
    material.refresh_from_db()
    assert material.name == "HTTP Updated"


def test_http_status_post_works_get_returns_405(app_client):
    user = _admin_actor()
    material = _material(active=True)
    _login(app_client, user)
    assert app_client.get(reverse("catalog:material-deactivate", args=[material.pk])).status_code == 405
    response = app_client.post(
        reverse("catalog:material-deactivate", args=[material.pk]),
        follow=True,
    )
    assert "Malzeme pasifleştirildi." in response.content.decode()
    material.refresh_from_db()
    assert material.active is False


def test_form_exact_fields_and_forged_post_ignored(app_client):
    user = _admin_actor()
    category = _category()
    unit = _unit()
    _login(app_client, user)
    form = app_client.get(reverse("catalog:material-create")).context["form"]
    assert list(form.fields) == [
        "material_code",
        "name",
        "category",
        "brand",
        "model",
        "unit",
        "tracking_mode",
        "minimum_stock_value",
    ]
    assert "active" not in form.fields
    assert "technical_specs" not in form.fields
    assert "id" not in form.fields
    assert "created_at" not in form.fields
    assert "updated_at" not in form.fields

    material = _material(
        technical_specs={"keep": "value"},
        active=False,
    )
    response = app_client.post(
        reverse("catalog:material-update", args=[material.pk]),
        {
            **_valid_material_post(
                material.category,
                material.unit,
                material_code=material.material_code,
                name="Forged Fields",
            ),
            "active": "true",
            "technical_specs": '{"hacked": true}',
            "id": str(uuid.uuid4()),
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2020-01-01T00:00:00Z",
        },
        follow=True,
    )
    assert response.status_code == 200
    material.refresh_from_db()
    assert material.name == "Forged Fields"
    assert material.active is False
    assert material.technical_specs == {"keep": "value"}


def test_anonymous_mutations_redirect_without_audit(app_client):
    material = _material()
    before_events = AuditEvent.objects.count()
    response = app_client.post(reverse("catalog:material-create"), {})
    assert response.status_code == 302
    parsed = urlparse(response.url)
    assert parsed.path == "/accounts/login/"
    assert parse_qs(parsed.query).get("next") == ["/catalog/materials/new/"]
    assert AuditEvent.objects.count() == before_events
    assert material.active is True


def test_unknown_uuid_update_and_status_return_404(app_client):
    user = _admin_actor()
    category = _category()
    unit = _unit()
    _login(app_client, user)
    missing = uuid.uuid4()
    before_events = AuditEvent.objects.count()
    assert app_client.get(reverse("catalog:material-update", args=[missing])).status_code == 404
    assert app_client.post(
        reverse("catalog:material-update", args=[missing]),
        _valid_material_post(category, unit),
    ).status_code == 404
    assert app_client.post(reverse("catalog:material-deactivate", args=[missing])).status_code == 404
    assert AuditEvent.objects.count() == before_events
