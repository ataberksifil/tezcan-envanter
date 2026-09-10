from __future__ import annotations

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.roles import (
    MANAGED_CATALOG_CODENAMES,
    ROLE_CATALOG_CODENAMES,
    ROLE_NAMES,
)


class Command(BaseCommand):
    help = (
        "Provision canonical application groups and reconcile catalog permissions "
        "for TECHNICIAN, STOREKEEPER, and ADMIN_MANAGER."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--database",
            default="default",
            help='Database alias to use (default: "default").',
        )

    def handle(self, *args, **options):
        database = options["database"]

        with transaction.atomic(using=database):
            permission_map = self._load_catalog_permissions(database)
            groups = self._get_or_create_groups(database)

            for role_name in ROLE_NAMES:
                group = groups[role_name]
                allowed_codenames = ROLE_CATALOG_CODENAMES[role_name]
                self._reconcile_group_catalog_permissions(
                    group,
                    permission_map,
                    allowed_codenames,
                    database,
                )

        self.stdout.write(
            self.style.SUCCESS(
                "Roles provisioned: TECHNICIAN, STOREKEEPER, ADMIN_MANAGER "
                "(catalog permissions reconciled)."
            )
        )

    def _load_catalog_permissions(self, database: str) -> dict[str, Permission]:
        catalog_content_types = ContentType.objects.using(database).filter(
            app_label="catalog",
            model__in=("category", "unitofmeasure", "material"),
        )
        permissions = Permission.objects.using(database).filter(
            content_type__in=catalog_content_types,
            codename__in=MANAGED_CATALOG_CODENAMES,
        )
        permission_map = {permission.codename: permission for permission in permissions}

        missing = sorted(MANAGED_CATALOG_CODENAMES - permission_map.keys())
        if missing:
            raise CommandError(
                "Missing expected catalog permissions; no role changes were applied. "
                f"Missing codenames: {', '.join(missing)}"
            )

        return permission_map

    def _get_or_create_groups(self, database: str) -> dict[str, Group]:
        groups: dict[str, Group] = {}
        for role_name in ROLE_NAMES:
            group, _created = Group.objects.using(database).get_or_create(name=role_name)
            groups[role_name] = group
        return groups

    def _reconcile_group_catalog_permissions(
        self,
        group: Group,
        permission_map: dict[str, Permission],
        allowed_codenames: frozenset[str],
        database: str,
    ) -> None:
        current_permissions = group.permissions.using(database).select_related(
            "content_type"
        )
        managed_current = [
            permission
            for permission in current_permissions
            if permission.codename in MANAGED_CATALOG_CODENAMES
            and permission.content_type.app_label == "catalog"
        ]
        if managed_current:
            group.permissions.remove(*managed_current)

        allowed_permissions = [permission_map[codename] for codename in sorted(allowed_codenames)]
        group.permissions.add(*allowed_permissions)
