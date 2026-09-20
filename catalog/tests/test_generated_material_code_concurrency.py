from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from catalog.models import Category, Material, UnitOfMeasure
from catalog.services.materials import create_material, is_generated_material_code


class GeneratedMaterialCodeConcurrencyTests(TransactionTestCase):
    databases = {"default"}

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.category = Category.objects.create(name=f"GEN-CAT-{suffix}")
        self.unit = UnitOfMeasure.objects.create(code=f"GEN-U-{suffix}", name="Adet")
        self.user = get_user_model().objects.create_user(username=f"gen-{suffix}")
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="catalog",
                codename="add_material",
            )
        )

    def _fixture_teardown(self):
        # Do not flush migration-owned MaterialCondition seeds. AuditEvent DELETE
        # is blocked; TRUNCATE is the same keepdb hygiene used by inventory
        # concurrency tests for immutable ledger tables.
        with connection.cursor() as cursor:
            cursor.execute("TRUNCATE TABLE audit_auditevent")
        Material.objects.filter(category_id=self.category.pk).delete()
        UnitOfMeasure.objects.filter(pk=self.unit.pk).delete()
        Category.objects.filter(pk=self.category.pk).delete()
        get_user_model().objects.filter(pk=self.user.pk).delete()
        close_old_connections()

    def test_concurrent_create_allocates_distinct_generated_codes(self):
        errors = []
        codes = []

        def worker(index):
            close_old_connections()
            try:
                result = create_material(
                    actor=self.user,
                    name=f"Concurrent {index}",
                    category_id=self.category.pk,
                    unit_id=self.unit.pk,
                    tracking_mode=Material.TrackingMode.QUANTITY,
                )
                codes.append(result.material.material_code)
            except Exception as exc:  # pragma: no cover - failure path asserted below
                errors.append(exc)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(worker, (1, 2)))

        assert errors == []
        assert len(codes) == 2
        assert codes[0] != codes[1]
        assert all(is_generated_material_code(code) for code in codes)
        assert Material.objects.filter(material_code__in=codes).count() == 2
