import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission

from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from inventory.services.receipts import receive_quantity
from locations.models import Location


def grant(user, *labels):
    for label in labels:
        app_label, codename = label.split(".", 1)
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label=app_label, codename=codename
            )
        )
    return get_user_model().objects.get(pk=user.pk)


@pytest.fixture
def correction_objects(db):
    suffix = uuid.uuid4().hex[:8]
    unit = UnitOfMeasure.objects.create(code=f"COR-U-{suffix}", name="Adet")
    category = Category.objects.create(name=f"Correction {suffix}")
    material = Material.objects.create(
        material_code=f"COR-M-{suffix}",
        name="Correction material",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    other_material = Material.objects.create(
        material_code=f"COR-M2-{suffix}",
        name="Other correction material",
        category=category,
        unit=unit,
        tracking_mode=Material.TrackingMode.QUANTITY,
    )
    condition = MaterialCondition.objects.create(
        code=f"COR-C-{suffix}", name="Good", sort_order=970
    )
    other_condition = MaterialCondition.objects.create(
        code=f"COR-C2-{suffix}", name="Other", sort_order=971
    )
    source = Location.objects.create(
        code=f"COR-L-{suffix}", name="Original", can_hold_stock=True
    )
    target = Location.objects.create(
        code=f"COR-L2-{suffix}", name="Corrected", can_hold_stock=True
    )
    requester = grant(
        get_user_model().objects.create_user(username=f"cor-request-{suffix}"),
        "corrections.add_correctionrequest",
        "corrections.view_correctionrequest",
        "inventory.view_inventorytransaction",
    )
    approver = grant(
        get_user_model().objects.create_user(username=f"cor-approve-{suffix}"),
        "corrections.add_correctionrequest",
        "corrections.view_correctionrequest",
        "corrections.decide_correctionrequest",
        "inventory.receive_stock",
        "inventory.view_inventorytransaction",
    )
    receipt = receive_quantity(
        actor=approver,
        operation_id=uuid.uuid4(),
        material_id=material.pk,
        unit_id=unit.pk,
        condition_id=condition.pk,
        target_location_id=source.pk,
        quantity=Decimal("10.000"),
    )
    return {
        "unit": unit,
        "category": category,
        "material": material,
        "other_material": other_material,
        "condition": condition,
        "other_condition": other_condition,
        "source": source,
        "target": target,
        "requester": requester,
        "approver": approver,
        "transaction": receipt.transaction,
        "line": receipt.lines[0],
    }
