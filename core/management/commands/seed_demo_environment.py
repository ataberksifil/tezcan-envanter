"""Bootstrap a deterministic boss-demo dataset via legitimate inventory services.

Local/dev demo only. Uses short operational codes (G1, H1) for location and
production-line labels; expanded master names stay out of ordinary UI surfaces.
Not production seed data; does not replace controlled count / baseline cutover.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Employee
from accounts.roles import (
    ADMIN_MANAGER,
    DEFAULT_ROLE_TEMPLATES,
    STOREKEEPER,
    TECHNICIAN,
    required_template_catalog_codenames,
)
from accounts.services.employees import create_employee
from catalog.models import Category, Material, MaterialCondition, UnitOfMeasure
from catalog.services.materials import create_material
from counting.models import PhysicalCountSession
from counting.services import create_physical_count_session
from inventory.models import InventoryTransaction, ProductionLine, SerializedAsset
from inventory.services.issues import issue_quantity
from inventory.services.production_lines import create_production_line
from inventory.services.receipts import receive_quantity, receive_serialized
from inventory.services.returns import return_quantity
from inventory.services.transfers import transfer_quantity
from locations.models import Location
from locations.services.locations import create_location

User = get_user_model()

DEMO_MARKER_LOCATION_CODE = "G1"
DEMO_SECONDARY_LOCATION_CODE = "G2"
# Local demo credential only; never use in production.
DEMO_PASSWORD = "Demo2026!"
DEMO_USERS = (
    (TECHNICIAN, "demo.teknisyen"),
    (STOREKEEPER, "demo.depocu"),
    (ADMIN_MANAGER, "demo.yonetici"),
)

# Deterministic operation IDs for idempotent re-runs (G1/H1 demo slice).
_DEMO_NS = uuid.UUID("d0000000-0001-4001-8001-000000000002")


def _demo_operation_id(label: str) -> uuid.UUID:
    return uuid.uuid5(_DEMO_NS, f"boss-demo-g1-h1/{label}")


DEMO_ROUTINE_COUNT_REF = "DEMO-SAYIM-G1"
DEMO_BASELINE_COUNT_REF = "DEMO-KESIM-G2"
DEMO_SERIALIZED_ASSET_CODE = "DEMO-G1-0001"


class Command(BaseCommand):
    help = (
        "Seed a deterministic boss-demo dataset (master data + sample ledger "
        "movements) using inventory services. Requires setup_roles first. "
        "Idempotent: skips stock seeding when demo marker location exists."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--database",
            default="default",
            help='Database alias to use (default: "default").',
        )
        parser.add_argument(
            "--reset-passwords",
            action="store_true",
            help="Reset demo user passwords even when demo data already exists.",
        )

    def handle(self, *args, **options):
        database = options["database"]
        verbosity = options["verbosity"]
        reset_passwords = options["reset_passwords"]

        call_command("setup_roles", database=database, verbosity=0)

        with transaction.atomic(using=database):
            self._ensure_demo_role_permissions(database)
            actor = self._ensure_demo_users(database, reset_passwords=reset_passwords)

            if self._demo_seed_complete(database):
                skipped_stock = True
            else:
                skipped_stock = False
                context = self._create_master_data(actor, using=database)
                self._seed_inventory_movements(actor, context, using=database)
            self._ensure_demo_count_sessions(actor, using=database)

        if verbosity >= 1:
            if skipped_stock:
                self.stdout.write(
                    self.style.WARNING(
                        "Demo inventory already seeded; skipping stock movements. "
                        "Use --reset-passwords to refresh demo credentials."
                    )
                )
            else:
                self.stdout.write(self.style.SUCCESS("Demo environment seeded."))
            self._print_credentials()
            self.stdout.write("Walkthrough: docs/DEMO-ENVIRONMENT.md")

    def _demo_seed_complete(self, database: str) -> bool:
        if Location.objects.using(database).filter(code=DEMO_MARKER_LOCATION_CODE).exists():
            return True
        return InventoryTransaction.objects.using(database).filter(
            operation_id=_demo_operation_id("receipt-klemens")
        ).exists()

    def _ensure_demo_role_permissions(self, database: str) -> None:
        """Ensure default role groups expose current template permissions for local demo."""
        required_codenames = required_template_catalog_codenames()
        content_types = ContentType.objects.using(database).filter(
            app_label__in=(
                "catalog",
                "locations",
                "accounts",
                "inventory",
                "corrections",
                "counting",
                "imports",
            ),
            model__in=(
                "category",
                "unitofmeasure",
                "material",
                "location",
                "employee",
                "productionline",
                "inventorytransaction",
                "stockbalance",
                "correctionrequest",
                "physicalcountsession",
                "physicalcountquantityline",
                "inventorybaseline",
            ),
        )
        permission_map = {
            permission.codename: permission
            for permission in Permission.objects.using(database).filter(
                content_type__in=content_types,
                codename__in=required_codenames,
            )
        }
        for role_name in (TECHNICIAN, STOREKEEPER, ADMIN_MANAGER):
            group = Group.objects.using(database).get(name=role_name)
            have = set(group.permissions.values_list("codename", flat=True))
            missing = sorted(DEFAULT_ROLE_TEMPLATES[role_name] - have)
            if missing:
                group.permissions.add(
                    *[permission_map[codename] for codename in missing]
                )

    def _ensure_demo_users(self, database: str, *, reset_passwords: bool):
        groups = {
            name: Group.objects.using(database).get(name=name)
            for name in (TECHNICIAN, STOREKEEPER, ADMIN_MANAGER)
        }

        admin_user = None
        for role_name, username in DEMO_USERS:
            user, created = User.objects.using(database).get_or_create(
                username=username,
                defaults={"is_staff": False, "is_superuser": False},
            )
            if created or reset_passwords:
                user.set_password(DEMO_PASSWORD)
                user.save(using=database)
            user.groups.add(groups[role_name])
            if role_name == ADMIN_MANAGER:
                admin_user = user

        if admin_user is None:
            raise CommandError("Failed to create demo admin user.")
        return User.objects.using(database).get(pk=admin_user.pk)

    def _load_reference_row(self, model, *, using: str, **lookup):
        try:
            return model.objects.using(using).get(**lookup)
        except model.DoesNotExist as exc:
            raise CommandError(
                "Catalog reference data missing. Run migrate before seed_demo_environment."
            ) from exc

    def _get_or_create_location(self, actor, *, code: str, name: str, using: str) -> Location:
        try:
            return Location.objects.using(using).get(code=code)
        except Location.DoesNotExist:
            return create_location(
                actor=actor,
                code=code,
                name=name,
                can_hold_stock=True,
                using=using,
            ).location

    def _get_or_create_material(
        self,
        actor,
        *,
        material_code: str,
        name: str,
        category_id,
        unit_id,
        tracking_mode: str,
        using: str,
    ) -> Material:
        try:
            return Material.objects.using(using).get(material_code=material_code)
        except Material.DoesNotExist:
            return create_material(
                actor=actor,
                material_code=material_code,
                name=name,
                category_id=category_id,
                unit_id=unit_id,
                tracking_mode=tracking_mode,
                using=using,
            ).material

    def _get_or_create_employee(self, actor, *, using: str) -> Employee:
        try:
            return Employee.objects.using(using).get(employee_number="DEMO-1001")
        except Employee.DoesNotExist:
            return create_employee(
                actor=actor,
                employee_number="DEMO-1001",
                first_name="Mehmet",
                last_name="Teknisyen",
                user_id=User.objects.using(using).get(username="demo.teknisyen").pk,
                using=using,
            ).employee

    def _get_or_create_production_line(self, actor, *, using: str) -> ProductionLine:
        try:
            return ProductionLine.objects.using(using).get(code="H1")
        except ProductionLine.DoesNotExist:
            return create_production_line(
                actor=actor,
                code="H1",
                name="H1",
                using=using,
            ).production_line

    def _create_master_data(self, actor, *, using: str) -> dict:
        unit = self._load_reference_row(UnitOfMeasure, using=using, code="ADET")
        category = self._load_reference_row(Category, using=using, name="Otomasyon")
        condition = self._load_reference_row(
            MaterialCondition, using=using, code="NEW_GOOD"
        )

        depot_a = self._get_or_create_location(
            actor,
            code=DEMO_MARKER_LOCATION_CODE,
            name=DEMO_MARKER_LOCATION_CODE,
            using=using,
        )
        depot_b = self._get_or_create_location(
            actor,
            code=DEMO_SECONDARY_LOCATION_CODE,
            name=DEMO_SECONDARY_LOCATION_CODE,
            using=using,
        )

        qty_material = self._get_or_create_material(
            actor,
            material_code="DEMO-KLEMENS",
            name="Demo terminal klemens",
            category_id=category.id,
            unit_id=unit.id,
            tracking_mode=Material.TrackingMode.QUANTITY,
            using=using,
        )
        serialized_material = self._get_or_create_material(
            actor,
            material_code="DEMO-PLC-CPU",
            name="Demo PLC CPU",
            category_id=category.id,
            unit_id=unit.id,
            tracking_mode=Material.TrackingMode.SERIALIZED,
            using=using,
        )

        employee = self._get_or_create_employee(actor, using=using)
        production_line = self._get_or_create_production_line(actor, using=using)

        return {
            "unit": unit,
            "category": category,
            "condition": condition,
            "depot_a": depot_a,
            "depot_b": depot_b,
            "qty_material": qty_material,
            "serialized_material": serialized_material,
            "employee": employee,
            "production_line": production_line,
            "storekeeper": User.objects.using(using).get(username="demo.depocu"),
            "technician": User.objects.using(using).get(username="demo.teknisyen"),
        }

    def _seed_inventory_movements(self, actor, context: dict, *, using: str) -> None:
        unit = context["unit"]
        condition = context["condition"]
        depot_a = context["depot_a"]
        depot_b = context["depot_b"]
        qty_material = context["qty_material"]
        serialized_material = context["serialized_material"]
        employee = context["employee"]
        production_line = context["production_line"]
        storekeeper = context["storekeeper"]
        technician = context["technician"]

        receive_quantity(
            actor=storekeeper,
            operation_id=_demo_operation_id("receipt-klemens"),
            material_id=qty_material.id,
            unit_id=unit.id,
            condition_id=condition.id,
            target_location_id=depot_a.id,
            quantity=Decimal("100.000"),
            using=using,
        )

        if not SerializedAsset.objects.using(using).filter(
            internal_asset_code=DEMO_SERIALIZED_ASSET_CODE
        ).exists():
            receive_serialized(
                actor=storekeeper,
                operation_id=_demo_operation_id("receipt-plc"),
                material_id=serialized_material.id,
                internal_asset_code=DEMO_SERIALIZED_ASSET_CODE,
                serial_number="SN-DEMO-G1-0001",
                condition_id=condition.id,
                target_location_id=depot_a.id,
                using=using,
            )

        issue_result = issue_quantity(
            actor=technician,
            operation_id=_demo_operation_id("issue-klemens"),
            material_id=qty_material.id,
            unit_id=unit.id,
            condition_id=condition.id,
            source_location_id=depot_a.id,
            quantity=Decimal("15.000"),
            receiver_employee_id=employee.id,
            production_line_id=production_line.id,
            usage_location_text="H1 montaj istasyonu",
            using=using,
        )

        return_quantity(
            actor=storekeeper,
            operation_id=_demo_operation_id("return-klemens"),
            original_issue_line_id=issue_result.lines[0].id,
            target_location_id=depot_a.id,
            quantity=Decimal("5.000"),
            using=using,
        )

        transfer_quantity(
            actor=storekeeper,
            operation_id=_demo_operation_id("transfer-klemens"),
            material_id=qty_material.id,
            unit_id=unit.id,
            condition_id=condition.id,
            source_location_id=depot_a.id,
            target_location_id=depot_b.id,
            quantity=Decimal("10.000"),
            using=using,
        )

    def _ensure_demo_count_sessions(self, actor, *, using: str) -> None:
        storekeeper = User.objects.using(using).get(username="demo.depocu")
        try:
            depot_a = Location.objects.using(using).get(code=DEMO_MARKER_LOCATION_CODE)
            depot_b = Location.objects.using(using).get(code=DEMO_SECONDARY_LOCATION_CODE)
        except Location.DoesNotExist:
            return
        if not PhysicalCountSession.objects.using(using).filter(
            reference_number=DEMO_ROUTINE_COUNT_REF
        ).exists():
            create_physical_count_session(
                actor=storekeeper,
                reference_number=DEMO_ROUTINE_COUNT_REF,
                scope_location_id=depot_a.pk,
                baseline_candidate=False,
                using=using,
            )
        if not PhysicalCountSession.objects.using(using).filter(
            reference_number=DEMO_BASELINE_COUNT_REF
        ).exists():
            create_physical_count_session(
                actor=storekeeper,
                reference_number=DEMO_BASELINE_COUNT_REF,
                scope_location_id=depot_b.pk,
                baseline_candidate=True,
                using=using,
            )

    def _print_credentials(self) -> None:
        self.stdout.write("")
        self.stdout.write("Demo kullanıcıları (yalnızca demo ortamı):")
        for _role, username in DEMO_USERS:
            self.stdout.write(f"  {username} / {DEMO_PASSWORD}")
        self.stdout.write("")
