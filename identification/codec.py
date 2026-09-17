"""Strict, deterministic V1 machine-readable identity codec.

The canonical payload is carrier-independent. Code 128 and QR encode the
same text; the carrier is not part of entity identity.

The compact token is only a reversible textual form of the existing UUID:
URL-safe Base64 of the raw 16 UUID bytes, with ``=`` padding removed.
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from dataclasses import dataclass
from enum import StrEnum

IDENTITY_PREFIX = "TZ"
IDENTITY_VERSION = "1"
IDENTITY_TOKEN_LENGTH = 22
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")

# Compatibility aliases used by the interrupted QR-first implementation.
QR_NAMESPACE = IDENTITY_PREFIX
QR_VERSION = IDENTITY_VERSION
IDENTITY_NAMESPACE = IDENTITY_PREFIX


class IdentityEntityType(StrEnum):
    MATERIAL = "M"
    SERIALIZED_ASSET = "A"
    LOCATION = "L"


QREntityType = IdentityEntityType


@dataclass(frozen=True, slots=True)
class CanonicalIdentity:
    entity_type: IdentityEntityType
    object_id: uuid.UUID

    @property
    def payload(self) -> str:
        return encode_payload(self.entity_type, self.object_id)

    @property
    def token(self) -> str:
        return encode_uuid_token(self.object_id)


QRIdentity = CanonicalIdentity


class IdentityCodecError(ValueError):
    code = "invalid_payload"
    user_message = "Kod geçersiz."


class IdentityMalformedPayload(IdentityCodecError):
    code = "malformed_payload"
    user_message = "Kod içeriği beklenen biçimde değil."


class IdentityWrongNamespace(IdentityCodecError):
    code = "wrong_namespace"
    user_message = "Bu kod Tezcan Envanter sistemine ait değil."


class IdentityUnsupportedVersion(IdentityCodecError):
    code = "unsupported_version"
    user_message = "Bu kod sürümü desteklenmiyor."


class IdentityUnsupportedEntityType(IdentityCodecError):
    code = "unsupported_entity_type"
    user_message = "Bu koddaki nesne türü desteklenmiyor."


class IdentityMalformedUUID(IdentityCodecError):
    code = "malformed_uuid"
    user_message = "Koddaki kimlik geçersiz."


QRCodecError = IdentityCodecError
QRMalformedPayload = IdentityMalformedPayload
QRWrongNamespace = IdentityWrongNamespace
QRUnsupportedVersion = IdentityUnsupportedVersion
QRUnsupportedEntityType = IdentityUnsupportedEntityType
QRMalformedUUID = IdentityMalformedUUID


def encode_uuid_token(object_id: uuid.UUID) -> str:
    if not isinstance(object_id, uuid.UUID):
        raise TypeError("object_id must be a UUID")
    token = base64.urlsafe_b64encode(object_id.bytes).decode("ascii").rstrip("=")
    if len(token) != IDENTITY_TOKEN_LENGTH:
        raise IdentityMalformedUUID
    return token


def decode_uuid_token(token: str) -> uuid.UUID:
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        raise IdentityMalformedUUID
    padding = "=" * ((4 - len(token) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + padding)
    except (ValueError, binascii.Error):
        raise IdentityMalformedUUID from None
    if len(raw) != 16:
        raise IdentityMalformedUUID
    object_id = uuid.UUID(bytes=raw)
    if encode_uuid_token(object_id) != token:
        raise IdentityMalformedUUID
    return object_id


def _prefix_for(entity_type: IdentityEntityType) -> str:
    return f"{IDENTITY_PREFIX}{IDENTITY_VERSION}{entity_type.value}"


def encode_payload(
    entity_type: IdentityEntityType,
    object_id: uuid.UUID,
) -> str:
    if not isinstance(entity_type, IdentityEntityType):
        raise TypeError("entity_type must be an IdentityEntityType")
    if not isinstance(object_id, uuid.UUID):
        raise TypeError("object_id must be a UUID")
    return f"{_prefix_for(entity_type)}:{encode_uuid_token(object_id)}"


def encode_material(material_id: uuid.UUID) -> str:
    return encode_payload(IdentityEntityType.MATERIAL, material_id)


def encode_serialized_asset(asset_id: uuid.UUID) -> str:
    return encode_payload(IdentityEntityType.SERIALIZED_ASSET, asset_id)


def encode_location(location_id: uuid.UUID) -> str:
    return encode_payload(IdentityEntityType.LOCATION, location_id)


def decode_payload(payload: str) -> CanonicalIdentity:
    if not isinstance(payload, str) or not payload:
        raise IdentityMalformedPayload
    if any(character.isspace() for character in payload):
        raise IdentityMalformedPayload
    if payload.count(":") != 1:
        raise IdentityMalformedPayload

    prefix, token = payload.split(":")
    if len(prefix) != 4:
        raise IdentityMalformedPayload
    if prefix[:2] != IDENTITY_PREFIX:
        raise IdentityWrongNamespace
    if prefix[2] != IDENTITY_VERSION:
        raise IdentityUnsupportedVersion
    try:
        entity_type = IdentityEntityType(prefix[3])
    except ValueError:
        raise IdentityUnsupportedEntityType from None

    object_id = decode_uuid_token(token)
    return CanonicalIdentity(entity_type=entity_type, object_id=object_id)


encode_qr_payload = encode_payload
encode_material_qr = encode_material
encode_serialized_asset_qr = encode_serialized_asset
encode_location_qr = encode_location
decode_qr_payload = decode_payload
