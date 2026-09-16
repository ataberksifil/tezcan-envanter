from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from django.core.exceptions import ValidationError

INVALID_EVIDENCE = "corrections.invalid_evidence"
MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
EVIDENCE_STORAGE_PREFIX = "corrections/evidence/"

_JPEG = "image/jpeg"
_PNG = "image/png"
_WEBP = "image/webp"

ALLOWED_CONTENT_TYPES = (_JPEG, _PNG, _WEBP)
CONTENT_TYPE_EXTENSIONS = {
    _JPEG: ".jpg",
    _PNG: ".png",
    _WEBP: ".webp",
}
EXTENSION_CONTENT_TYPES = {
    ".jpg": _JPEG,
    ".jpeg": _JPEG,
    ".png": _PNG,
    ".webp": _WEBP,
}
HEIF_BRANDS = {
    b"avci",
    b"avcs",
    b"heic",
    b"heif",
    b"heim",
    b"heis",
    b"heix",
    b"hevc",
    b"hevx",
    b"mif1",
    b"msf1",
}
_HEIC_MESSAGE = (
    "HEIC/HEIF dosyaları V1'de desteklenmez. JPEG, PNG veya WebP olarak "
    "yükleyin ya da cihazınızdan bu biçimlerden birine dışa aktarın."
)


@dataclass(frozen=True)
class ValidatedEvidence:
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    content: bytes
    extension: str


def validate_evidence_files(uploads) -> list[ValidatedEvidence]:
    files = _as_file_list(uploads)
    if not files:
        raise ValidationError(
            "Yeni düzeltme talebi için en az bir kanıt fotoğrafı zorunludur.",
            code=INVALID_EVIDENCE,
        )
    return [validate_evidence_file(upload) for upload in files]


def validate_evidence_file(upload) -> ValidatedEvidence:
    original_filename = snapshot_original_filename(getattr(upload, "name", "") or "")
    reported_size = getattr(upload, "size", None)
    if reported_size is not None and reported_size > MAX_EVIDENCE_BYTES:
        raise ValidationError(
            "Kanıt fotoğrafı en fazla 10 MiB olabilir.",
            code=INVALID_EVIDENCE,
        )
    content = _read_upload(upload)
    if not content:
        raise ValidationError("Kanıt fotoğrafı boş olamaz.", code=INVALID_EVIDENCE)
    if len(content) > MAX_EVIDENCE_BYTES:
        raise ValidationError(
            "Kanıt fotoğrafı en fazla 10 MiB olabilir.",
            code=INVALID_EVIDENCE,
        )

    kind = detect_image_kind(content)
    extension = _filename_extension(original_filename)
    if kind in {"heif"} or extension in {".heic", ".heif"}:
        raise ValidationError(_HEIC_MESSAGE, code=INVALID_EVIDENCE)
    if kind == "gif" or extension == ".gif":
        raise ValidationError(
            "GIF dosyaları kabul edilmez. JPEG, PNG veya WebP yükleyin.",
            code=INVALID_EVIDENCE,
        )
    if kind == "svg" or extension == ".svg":
        raise ValidationError(
            "SVG dosyaları kabul edilmez. JPEG, PNG veya WebP yükleyin.",
            code=INVALID_EVIDENCE,
        )
    if kind == "pdf" or extension == ".pdf":
        raise ValidationError(
            "PDF dosyaları kabul edilmez. JPEG, PNG veya WebP yükleyin.",
            code=INVALID_EVIDENCE,
        )
    detected_type = {
        "jpeg": _JPEG,
        "png": _PNG,
        "webp": _WEBP,
    }.get(kind)
    if detected_type is None:
        raise ValidationError(
            "Kanıt fotoğrafı JPEG, PNG veya WebP olmalıdır.",
            code=INVALID_EVIDENCE,
        )
    if extension in EXTENSION_CONTENT_TYPES and EXTENSION_CONTENT_TYPES[extension] != detected_type:
        raise ValidationError(
            "Dosya uzantısı gerçek içerik türüyle uyuşmuyor.",
            code=INVALID_EVIDENCE,
        )
    return ValidatedEvidence(
        original_filename=original_filename,
        content_type=detected_type,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        content=content,
        extension=CONTENT_TYPE_EXTENSIONS[detected_type],
    )


def snapshot_original_filename(name: str) -> str:
    if not isinstance(name, str):
        return "unnamed"
    normalized = name.replace("\\", "/").replace("\x00", "").strip()
    base = normalized.rsplit("/", 1)[-1].strip()
    if not base or base in {".", ".."}:
        return "unnamed"
    return base[:255]


def detect_image_kind(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "webp"
    if content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
        return "gif"
    if content.startswith(b"%PDF"):
        return "pdf"
    if _is_heif(content):
        return "heif"
    if _looks_like_svg(content):
        return "svg"
    if _looks_like_html(content):
        return "html"
    return None


def evidence_storage_key(evidence_id, extension: str) -> str:
    if extension not in {".jpg", ".png", ".webp"}:
        extension = ".jpg"
    return f"{EVIDENCE_STORAGE_PREFIX}{evidence_id}{extension}"


def _as_file_list(uploads):
    if uploads is None:
        return []
    if isinstance(uploads, (list, tuple)):
        return [item for item in uploads if item is not None]
    return [uploads]


def _read_upload(upload) -> bytes:
    if isinstance(upload, (bytes, bytearray)):
        return bytes(upload)
    read = getattr(upload, "read", None)
    if not callable(read):
        raise ValidationError("Kanıt dosyası okunamadı.", code=INVALID_EVIDENCE)
    content = read()
    seek = getattr(upload, "seek", None)
    if callable(seek):
        try:
            seek(0)
        except (OSError, ValueError):
            pass
    if not isinstance(content, (bytes, bytearray)):
        raise ValidationError("Kanıt dosyası okunamadı.", code=INVALID_EVIDENCE)
    return bytes(content)


def _filename_extension(filename: str) -> str:
    match = re.search(r"(\.[A-Za-z0-9]+)$", filename)
    if not match:
        return ""
    return match.group(1).lower()


def _is_heif(content: bytes) -> bool:
    if len(content) < 12 or content[4:8] != b"ftyp":
        return False
    brands = {content[8:12]}
    offset = 16
    while offset + 4 <= min(len(content), 64):
        brands.add(content[offset : offset + 4])
        offset += 4
    return bool(brands & HEIF_BRANDS)


def _looks_like_svg(content: bytes) -> bool:
    head = _text_head(content)
    return head.startswith("<svg") or (
        head.startswith("<?xml") and "<svg" in head
    ) or head.startswith("<!doctype svg")


def _looks_like_html(content: bytes) -> bool:
    head = _text_head(content)
    return (
        head.startswith("<html")
        or head.startswith("<!doctype html")
        or head.startswith("<head")
        or head.startswith("<body")
    )


def _text_head(content: bytes) -> str:
    sample = content[:512]
    if sample.startswith(b"\xef\xbb\xbf"):
        sample = sample[3:]
    return sample.lstrip().lower().decode("latin-1", errors="ignore")
