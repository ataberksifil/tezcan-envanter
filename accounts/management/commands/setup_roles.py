from __future__ import annotations

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.roles import (
    DEFAULT_ROLE_NAMES,
    DEFAULT_ROLE_TEMPLATES,
    required_template_catalog_codenames,
)


class Command(BaseCommand):
    help = (
        "Bootstrap missing default role templates "
        "(TECHNICIAN, STOREKEEPER, ADMIN_MANAGER). "
        "Existing groups are left unchanged, including administrator "
        "permission customizations. This is deployment/bootstrap "
        "provisioning and does not write AuditEvent rows."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--database",
            default="default",
            help='Database alias to use (default: "default").',
        )

    def handle(self, *args, **options):
        database = options["database"]
        verbosity = options["verbosity"]

        with transaction.atomic(using=database):
            permission_map = self._load_required_template_permissions(database)
            created, preserved = self._bootstrap_default_roles(
                database,
                permission_map,
            )

        if verbosity >= 1:
            self.stdout.write(
                f"created: {self._format_names(created)}"
            )
            self.stdout.write(
                f"preserved existing: {self._format_names(preserved)}"
            )

    def _load_required_template_permissions(self, database: str) -> dict[str, Permission]:
        required_codenames = required_template_catalog_codenames()
        content_types = ContentType.objects.using(database).filter(
            app_label__in=("catalog", "locations", "accounts", "inventory"),
            model__in=(
                "category",
                "unitofmeasure",
                "material",
                "location",
                "employee",
                "productionline",
                "inventorytransaction",
            ),
        )
        permissions = Permission.objects.using(database).filter(
            content_type__in=content_types,
            codename__in=required_codenames,
        )
        permission_map = {permission.codename: permission for permission in permissions}

        missing = sorted(required_codenames - permission_map.keys())
        if missing:
            raise CommandError(
                "Missing expected template permissions; no role changes were applied. "
                f"Missing codenames: {', '.join(missing)}"
            )

        return permission_map

    def _bootstrap_default_roles(
        self,
        database: str,
        permission_map: dict[str, Permission],
    ) -> tuple[list[str], list[str]]:
        created: list[str] = []
        preserved: list[str] = []

        for role_name in DEFAULT_ROLE_NAMES:
            group, was_created = Group.objects.using(database).get_or_create(
                name=role_name
            )
            if was_created:
                template_codenames = DEFAULT_ROLE_TEMPLATES[role_name]
                template_permissions = [
                    permission_map[codename] for codename in sorted(template_codenames)
                ]
                group.permissions.add(*template_permissions)
                created.append(role_name)
            else:
                preserved.append(role_name)

        created.sort()
        preserved.sort()
        return created, preserved

    @staticmethod
    def _format_names(names: list[str]) -> str:
        if not names:
            return "(none)"
        return ", ".join(names)
