import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class ProductionLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="children",
        db_index=False,
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["parent"], name="inventory_pl_parent_idx"),
            models.Index(fields=["name"], name="inventory_pl_name_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["code"],
                name="inventory_productionline_code_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(code__regex=r"^\s*$"),
                name="inventory_productionline_code_nonblank",
            ),
            models.CheckConstraint(
                condition=~Q(name__regex=r"^\s*$"),
                name="inventory_productionline_name_nonblank",
            ),
            models.CheckConstraint(
                condition=Q(parent__isnull=True) | ~Q(parent_id=F("id")),
                name="inventory_productionline_parent_not_self",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()

        if self.code is not None:
            self.code = self.code.strip()
            if not self.code:
                raise ValidationError({"code": "Üretim hattı kodu boş olamaz."})

        if self.name is not None:
            self.name = self.name.strip()
            if not self.name:
                raise ValidationError({"name": "Üretim hattı adı boş olamaz."})

        if self.parent_id is not None and self.pk is not None and self.parent_id == self.pk:
            raise ValidationError(
                {"parent": "Üretim hattı kendi üst hattı olamaz."}
            )

        if self.parent_id is not None:
            ancestor = self.parent
            visited: set[uuid.UUID] = set()
            if self.pk is not None:
                visited.add(self.pk)
            while ancestor is not None:
                if ancestor.pk in visited:
                    raise ValidationError(
                        {"parent": "Üretim hattı hiyerarşisinde döngü oluşturulamaz."}
                    )
                visited.add(ancestor.pk)
                ancestor = ancestor.parent
