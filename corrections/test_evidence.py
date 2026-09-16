import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.urls import reverse

from core.storage import persist_private_file, private_file_exists
from corrections.conftest import (
    BINARY_BYTES,
    GIF_BYTES,
    HEIC_BYTES,
    HTML_BYTES,
    JPEG_BYTES,
    PDF_BYTES,
    PNG_BYTES,
    SVG_BYTES,
    WEBP_BYTES,
    jpeg_upload,
    png_upload,
    webp_upload,
)
from corrections.evidence import MAX_EVIDENCE_BYTES, snapshot_original_filename
from corrections.models import CorrectionEvidence, CorrectionRequest
from corrections.services import (
    approve_correction_request,
    create_correction_request,
    reject_correction_request,
)
from corrections.test_services import balance, make_request
from inventory.models import InventoryTransaction

pytestmark = pytest.mark.django_db


def legacy_request(objects, **overrides):
    values = {
        "original_transaction": objects["transaction"],
        "original_line": objects["line"],
        "original_location": objects["source"],
        "requester": objects["requester"],
        "explanation": "Eski kanıtsız düzeltme açıklaması",
        "effect_type": CorrectionRequest.EffectType.QUANTITY,
        "quantity_effect": Decimal("1.000"),
        "status": CorrectionRequest.Status.PENDING,
    }
    values.update(overrides)
    record = CorrectionRequest(**values)
    record.save()
    return record


def test_create_without_evidence_is_rejected(correction_objects):
    with pytest.raises(ValidationError) as exc_info:
        make_request(correction_objects, evidence_files=[])
    assert exc_info.value.code == "corrections.invalid_evidence"
    assert CorrectionRequest.objects.count() == 0
    assert CorrectionEvidence.objects.count() == 0
    assert InventoryTransaction.objects.count() == 1


def test_one_valid_jpeg_creates_evidence_without_inventory_effect(correction_objects):
    before = InventoryTransaction.objects.count()
    request = make_request(correction_objects, evidence_files=[jpeg_upload("photo.jpg")])
    evidence = request.evidence.get()
    assert request.status == CorrectionRequest.Status.PENDING
    assert evidence.original_filename == "photo.jpg"
    assert evidence.content_type == "image/jpeg"
    assert evidence.size_bytes == len(JPEG_BYTES)
    assert evidence.sha256
    assert evidence.storage_key.startswith("corrections/evidence/")
    assert evidence.storage_key.endswith(".jpg")
    assert ".." not in evidence.storage_key
    assert evidence.uploaded_by_id == correction_objects["requester"].pk
    assert private_file_exists(evidence.storage_key)
    assert InventoryTransaction.objects.count() == before
    assert balance(correction_objects) == Decimal("10.000")


@pytest.mark.parametrize(
    "upload, content_type, suffix",
    [
        (jpeg_upload(), "image/jpeg", ".jpg"),
        (png_upload(), "image/png", ".png"),
        (webp_upload(), "image/webp", ".webp"),
    ],
)
def test_accepted_image_formats(correction_objects, upload, content_type, suffix):
    request = make_request(correction_objects, evidence_files=[upload])
    evidence = request.evidence.get()
    assert evidence.content_type == content_type
    assert evidence.storage_key.endswith(suffix)


def test_multiple_valid_photos_are_attached(correction_objects):
    request = make_request(
        correction_objects,
        evidence_files=[
            jpeg_upload("one.jpg"),
            png_upload("two.png"),
            webp_upload("three.webp"),
        ],
    )
    names = list(
        request.evidence.order_by("created_at").values_list("original_filename", flat=True)
    )
    assert names == ["one.jpg", "two.png", "three.webp"]


def test_invalid_file_among_multiple_creates_no_partial_request(correction_objects):
    with pytest.raises(ValidationError):
        make_request(
            correction_objects,
            evidence_files=[
                jpeg_upload("good.jpg"),
                SimpleUploadedFile("bad.gif", GIF_BYTES, content_type="image/gif"),
            ],
        )
    assert CorrectionRequest.objects.count() == 0
    assert CorrectionEvidence.objects.count() == 0


def test_traversal_filename_cannot_influence_storage_path(correction_objects):
    upload = jpeg_upload(r"..\..\etc\passwd.jpg")
    request = make_request(correction_objects, evidence_files=[upload])
    evidence = request.evidence.get()
    assert snapshot_original_filename(r"..\..\etc\passwd.jpg") == "passwd.jpg"
    assert evidence.original_filename == "passwd.jpg"
    assert evidence.storage_key.startswith("corrections/evidence/")
    assert ".." not in evidence.storage_key
    assert "etc" not in evidence.storage_key
    assert "passwd" not in evidence.storage_key


@pytest.mark.parametrize(
    "name, content, content_type",
    [
        ("photo.heic", HEIC_BYTES, "image/heic"),
        ("photo.heif", HEIC_BYTES, "image/heif"),
        ("photo.gif", GIF_BYTES, "image/gif"),
        ("photo.svg", SVG_BYTES, "image/svg+xml"),
        ("photo.pdf", PDF_BYTES, "application/pdf"),
        ("photo.jpg", HTML_BYTES, "image/jpeg"),
        ("photo.jpg", BINARY_BYTES, "image/jpeg"),
        ("photo.jpg", b"", "image/jpeg"),
        ("photo.png", JPEG_BYTES, "image/jpeg"),
    ],
)
def test_rejected_uploads(correction_objects, name, content, content_type):
    upload = SimpleUploadedFile(name, content, content_type=content_type)
    with pytest.raises(ValidationError) as exc_info:
        make_request(correction_objects, evidence_files=[upload])
    assert exc_info.value.code == "corrections.invalid_evidence"
    assert CorrectionRequest.objects.count() == 0


def test_heic_error_asks_for_export(correction_objects):
    upload = SimpleUploadedFile("phone.heic", HEIC_BYTES, content_type="image/heic")
    with pytest.raises(ValidationError) as exc_info:
        make_request(correction_objects, evidence_files=[upload])
    assert "HEIC/HEIF" in exc_info.value.messages[0]
    assert "JPEG" in exc_info.value.messages[0]


def test_oversize_upload_is_rejected(correction_objects, monkeypatch):
    monkeypatch.setattr("corrections.evidence.MAX_EVIDENCE_BYTES", 16)
    with pytest.raises(ValidationError) as exc_info:
        make_request(correction_objects, evidence_files=[jpeg_upload()])
    assert exc_info.value.code == "corrections.invalid_evidence"
    assert CorrectionRequest.objects.count() == 0


def test_evidence_metadata_is_immutable(correction_objects):
    request = make_request(correction_objects)
    evidence = request.evidence.get()
    evidence.original_filename = "changed.jpg"
    with pytest.raises(ValidationError):
        evidence.save()
    with pytest.raises(ValidationError):
        evidence.delete()
    with pytest.raises(DatabaseError, match="cannot be updated or deleted"):
        with transaction.atomic():
            CorrectionEvidence.objects.filter(pk=evidence.pk).update(
                original_filename="changed.jpg"
            )
    with pytest.raises(DatabaseError, match="cannot be updated or deleted"):
        with transaction.atomic():
            CorrectionEvidence.objects.filter(pk=evidence.pk).delete()
    evidence.refresh_from_db()
    assert evidence.original_filename == "evidence.jpg"


def test_approval_and_rejection_retain_evidence(correction_objects):
    approved = make_request(correction_objects, quantity_effect=Decimal("1.000"))
    approve_correction_request(
        actor=correction_objects["approver"],
        correction_request_id=approved.pk,
    )
    rejected = make_request(
        correction_objects,
        quantity_effect=Decimal("-1.000"),
    )
    reject_correction_request(
        actor=correction_objects["approver"],
        correction_request_id=rejected.pk,
        rejection_reason="Uygun değil çünkü kanıt incelendi",
    )
    assert approved.evidence.count() == 1
    assert rejected.evidence.count() == 1
    approved.refresh_from_db()
    rejected.refresh_from_db()
    assert approved.status == CorrectionRequest.Status.APPROVED
    assert rejected.status == CorrectionRequest.Status.REJECTED


def test_self_decision_still_blocked_with_evidence(correction_objects):
    from django.contrib.auth.models import Permission

    correction_objects["requester"].user_permissions.add(
        Permission.objects.get(
            content_type__app_label="corrections",
            codename="decide_correctionrequest",
        )
    )
    requester = get_user_model().objects.get(pk=correction_objects["requester"].pk)
    request = make_request(correction_objects, actor=requester)
    with pytest.raises(ValidationError) as exc_info:
        approve_correction_request(actor=requester, correction_request_id=request.pk)
    assert exc_info.value.code == "corrections.self_decision"


def test_legacy_request_without_evidence_is_viewable_and_decidable(
    client, correction_objects
):
    request = legacy_request(correction_objects)
    assert request.evidence.count() == 0
    client.force_login(correction_objects["approver"])
    response = client.get(reverse("corrections:request-detail", args=[request.pk]))
    assert response.status_code == 200
    assert "kayıtlı kanıt fotoğrafı yoktur" in response.content.decode()
    result = approve_correction_request(
        actor=correction_objects["approver"], correction_request_id=request.pk
    )
    assert result.transaction is not None
    request.refresh_from_db()
    assert request.status == CorrectionRequest.Status.APPROVED
    assert request.evidence.count() == 0


def test_storage_save_failure_does_not_commit_request(correction_objects, monkeypatch):
    def boom(storage_key, content):
        raise OSError("disk full")

    monkeypatch.setattr("corrections.services.persist_private_file", boom)
    with pytest.raises(OSError):
        make_request(correction_objects)
    assert CorrectionRequest.objects.count() == 0
    assert CorrectionEvidence.objects.count() == 0


def test_metadata_failure_after_file_write_does_not_commit_reference(
    correction_objects, monkeypatch
):
    saved = []
    real_persist = persist_private_file

    def tracking_persist(storage_key, content):
        name = real_persist(storage_key, content)
        saved.append(name)
        return name

    monkeypatch.setattr("corrections.services.persist_private_file", tracking_persist)

    def fail_save(self, *args, **kwargs):
        raise IntegrityError("simulated metadata failure")

    monkeypatch.setattr(CorrectionEvidence, "save", fail_save)
    with pytest.raises(IntegrityError):
        make_request(correction_objects)
    assert CorrectionRequest.objects.count() == 0
    assert CorrectionEvidence.objects.count() == 0
    assert saved


def test_anonymous_evidence_retrieval_is_denied(client, correction_objects):
    request = make_request(correction_objects)
    evidence = request.evidence.get()
    response = client.get(reverse("corrections:evidence-download", args=[evidence.pk]))
    assert response.status_code == 302
    assert "login" in response.url


def test_authenticated_without_view_permission_gets_403(client, correction_objects):
    request = make_request(correction_objects)
    evidence = request.evidence.get()
    user = get_user_model().objects.create_user(username="no-corr-view")
    client.force_login(user)
    response = client.get(reverse("corrections:evidence-download", args=[evidence.pk]))
    assert response.status_code == 403
    assert evidence.storage_key.encode() not in response.content


def test_authorized_view_returns_protected_image(client, correction_objects):
    request = make_request(correction_objects, evidence_files=[jpeg_upload("safe.jpg")])
    evidence = request.evidence.get()
    client.force_login(correction_objects["approver"])
    response = client.get(reverse("corrections:evidence-download", args=[evidence.pk]))
    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Content-Length"] == str(evidence.size_bytes)
    body = b"".join(response.streaming_content)
    assert body == JPEG_BYTES
    content_disp = response["Content-Disposition"]
    assert "inline" in content_disp
    assert "safe.jpg" in content_disp
    assert evidence.storage_key not in content_disp
    assert str(Path("var") / "private_media") not in content_disp


def test_guessed_uuid_does_not_bypass_permission(client, correction_objects):
    user = get_user_model().objects.create_user(username="guess-uuid")
    client.force_login(user)
    response = client.get(
        reverse("corrections:evidence-download", args=[uuid.uuid4()])
    )
    assert response.status_code == 403


def test_missing_evidence_for_authorized_user_is_404(client, correction_objects):
    client.force_login(correction_objects["approver"])
    response = client.get(
        reverse("corrections:evidence-download", args=[uuid.uuid4()])
    )
    assert response.status_code == 404
    assert b"corrections/evidence/" not in response.content


def test_approved_and_rejected_evidence_remain_retrievable(client, correction_objects):
    approved = make_request(correction_objects, quantity_effect=Decimal("1.000"))
    approve_correction_request(
        actor=correction_objects["approver"], correction_request_id=approved.pk
    )
    rejected = make_request(
        correction_objects,
        quantity_effect=Decimal("-1.000"),
    )
    reject_correction_request(
        actor=correction_objects["approver"], correction_request_id=rejected.pk
    )
    client.force_login(correction_objects["approver"])
    for record in (approved, rejected):
        evidence = record.evidence.get()
        response = client.get(
            reverse("corrections:evidence-download", args=[evidence.pk])
        )
        assert response.status_code == 200
        assert response["X-Content-Type-Options"] == "nosniff"


def test_detail_template_uses_protected_url_not_public_media(
    client, correction_objects, settings
):
    request = make_request(
        correction_objects, evidence_files=[jpeg_upload("photo & copy.jpg")]
    )
    evidence = request.evidence.get()
    client.force_login(correction_objects["approver"])
    response = client.get(reverse("corrections:request-detail", args=[request.pk]))
    content = response.content.decode()
    assert response.status_code == 200
    assert reverse("corrections:evidence-download", args=[evidence.pk]) in content
    assert evidence.storage_key not in content
    assert f"/{settings.MEDIA_URL}" not in content
    assert "photo & copy.jpg" not in content
    assert "photo &amp; copy.jpg" in content


def test_create_form_documents_formats_and_heic_limit(client, correction_objects):
    client.force_login(correction_objects["requester"])
    response = client.get(
        reverse(
            "corrections:request-create",
            args=[correction_objects["transaction"].pk],
        )
    )
    content = response.content.decode()
    assert response.status_code == 200
    assert "JPEG" in content and "PNG" in content and "WebP" in content
    assert "HEIC" in content
    assert "10 MiB" in content
    assert 'enctype="multipart/form-data"' in content


def test_create_without_file_is_rejected_in_ui(client, correction_objects):
    client.force_login(correction_objects["requester"])
    response = client.post(
        reverse(
            "corrections:request-create",
            args=[correction_objects["transaction"].pk],
        ),
        {
            "original_line": correction_objects["line"].pk,
            "original_location": correction_objects["source"].pk,
            "effect_type": "QUANTITY",
            "quantity_effect": "1.000",
            "explanation": "Kanıtsız talep denemesi burada",
        },
    )
    assert response.status_code == 200
    assert CorrectionRequest.objects.count() == 0


def test_admin_evidence_is_read_only(correction_objects):
    from django.contrib.admin.sites import site
    from django.test import RequestFactory

    request = make_request(correction_objects)
    evidence = request.evidence.get()
    admin_model = site._registry[CorrectionEvidence]
    staff = get_user_model().objects.create_superuser(
        username="corr-admin", password="x"
    )
    dummy = RequestFactory().get("/admin/")
    dummy.user = staff
    assert admin_model.has_add_permission(dummy) is False
    assert admin_model.has_change_permission(dummy, evidence) is False
    assert admin_model.has_delete_permission(dummy, evidence) is False
    assert admin_model.actions is None


def test_max_file_constant_is_10_mib():
    assert MAX_EVIDENCE_BYTES == 10 * 1024 * 1024


def test_evidence_reverse_sql_fails_closed_when_rows_exist(correction_objects):
    import importlib

    migration = importlib.import_module("corrections.migrations.0002_correction_evidence")

    make_request(correction_objects)
    with pytest.raises(DatabaseError, match="cannot reverse correction evidence"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(migration.CORRECTION_EVIDENCE_GUARD_REVERSE_SQL)
    assert CorrectionEvidence.objects.count() == 1
