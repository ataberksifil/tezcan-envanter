from __future__ import annotations

import base64
import uuid

import pytest

from identification.codec import (
    IDENTITY_PREFIX,
    IDENTITY_TOKEN_LENGTH,
    IDENTITY_VERSION,
    CanonicalIdentity,
    IdentityEntityType,
    IdentityMalformedPayload,
    IdentityMalformedUUID,
    IdentityUnsupportedEntityType,
    IdentityUnsupportedVersion,
    IdentityWrongNamespace,
    QREntityType,
    QRMalformedPayload,
    QRMalformedUUID,
    QRUnsupportedEntityType,
    QRUnsupportedVersion,
    QRWrongNamespace,
    decode_payload,
    decode_qr_payload,
    decode_uuid_token,
    encode_location,
    encode_location_qr,
    encode_material,
    encode_material_qr,
    encode_payload,
    encode_qr_payload,
    encode_serialized_asset,
    encode_serialized_asset_qr,
    encode_uuid_token,
)

NIL_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")
MAX_UUID = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
LEADING_ZERO_UUID = uuid.UUID("00000000-0000-4000-8000-000000000001")


def _canonical(entity_type: IdentityEntityType, object_id: uuid.UUID) -> str:
    return f"TZ1{entity_type.value}:{encode_uuid_token(object_id)}"


@pytest.mark.parametrize(
    "object_id",
    (
        NIL_UUID,
        MAX_UUID,
        LEADING_ZERO_UUID,
        uuid.UUID("18a8df7e-d5b8-4ebb-93d7-47d914b90db0"),
        uuid.uuid4(),
    ),
)
def test_uuid_token_is_exactly_22_chars_and_round_trips(object_id):
    token = encode_uuid_token(object_id)
    assert len(token) == IDENTITY_TOKEN_LENGTH == 22
    assert "=" not in token
    assert decode_uuid_token(token) == object_id
    assert encode_uuid_token(decode_uuid_token(token)) == token


def test_uuid_token_encoding_is_deterministic_and_unique():
    left = uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
    right = uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeefe")
    assert encode_uuid_token(left) == encode_uuid_token(left)
    assert encode_uuid_token(left) != encode_uuid_token(right)
    assert encode_uuid_token(NIL_UUID) == "AAAAAAAAAAAAAAAAAAAAAA"


@pytest.mark.parametrize(
    ("encoder", "entity_type"),
    (
        (encode_material, IdentityEntityType.MATERIAL),
        (encode_serialized_asset, IdentityEntityType.SERIALIZED_ASSET),
        (encode_location, IdentityEntityType.LOCATION),
        (encode_material_qr, QREntityType.MATERIAL),
        (encode_serialized_asset_qr, QREntityType.SERIALIZED_ASSET),
        (encode_location_qr, QREntityType.LOCATION),
    ),
)
def test_encode_decode_round_trip(encoder, entity_type):
    object_id = uuid.uuid4()
    payload = encoder(object_id)

    assert payload == _canonical(entity_type, object_id)
    assert payload.startswith(f"TZ1{entity_type.value}:")
    assert len(payload) == 4 + 1 + 22
    decoded = decode_payload(payload)
    assert decoded.entity_type == entity_type
    assert decoded.object_id == object_id
    assert decode_qr_payload(payload) == decoded
    assert decoded.payload == payload
    assert decoded.token == encode_uuid_token(object_id)


def test_qr_aliases_use_the_same_canonical_payload():
    object_id = uuid.uuid4()
    assert encode_material(object_id) == encode_material_qr(object_id)
    assert encode_serialized_asset(object_id) == encode_serialized_asset_qr(object_id)
    assert encode_location(object_id) == encode_location_qr(object_id)
    assert encode_payload(IdentityEntityType.MATERIAL, object_id) == encode_qr_payload(
        QREntityType.MATERIAL, object_id
    )
    assert IDENTITY_PREFIX == "TZ"
    assert IDENTITY_VERSION == "1"
    assert CanonicalIdentity(
        entity_type=IdentityEntityType.LOCATION, object_id=object_id
    ).payload == encode_location(object_id)


def test_encode_requires_typed_entity_and_uuid():
    with pytest.raises(TypeError):
        encode_payload("M", uuid.uuid4())
    with pytest.raises(TypeError):
        encode_payload(IdentityEntityType.MATERIAL, "not-a-uuid")


def test_old_verbose_draft_format_is_rejected():
    object_id = uuid.uuid4()
    with pytest.raises(IdentityMalformedPayload):
        decode_payload(f"TEZCAN:1:M:{object_id}")
    with pytest.raises(IdentityMalformedPayload):
        decode_payload(f"TEZCAN:1:A:{object_id}")
    with pytest.raises(IdentityMalformedPayload):
        decode_payload(f"TEZCAN:1:L:{object_id}")


def _valid_material_payload() -> str:
    return encode_material(uuid.uuid4())


def _valid_token() -> str:
    return encode_uuid_token(uuid.uuid4())


@pytest.mark.parametrize(
    ("payload", "error"),
    (
        ("", IdentityMalformedPayload),
        ("random text", IdentityMalformedPayload),
        ("TZ1M", IdentityMalformedPayload),
        ("TZ1M:", IdentityMalformedUUID),
        (f"TZ1M:{'A' * 21}", IdentityMalformedUUID),
        (f"TZ1M:{'A' * 23}", IdentityMalformedUUID),
        (f"TZ1M:{'A' * 22}==", IdentityMalformedUUID),
        (f"TZ1M:{'A' * 22}=", IdentityMalformedUUID),
        ("TZ1M:AAAAAAAAAAAAAAAAAAAAA+", IdentityMalformedUUID),
        ("TZ1M:AAAAAAAAAAAAAAAAAAAAA/", IdentityMalformedUUID),
        ("TZ1M:AAAAAAAAAAAAAAAAAAAAA!", IdentityMalformedUUID),
        (" TZ1M:AAAAAAAAAAAAAAAAAAAAAA", IdentityMalformedPayload),
        ("TZ1M:AAAAAAAAAAAAAAAAAAAAAA ", IdentityMalformedPayload),
        ("TZ1M: AAAAAAAAAAAAAAAAAAAAAA", IdentityMalformedPayload),
        ("XX1M:AAAAAAAAAAAAAAAAAAAAAA", IdentityWrongNamespace),
        ("tz1M:AAAAAAAAAAAAAAAAAAAAAA", IdentityWrongNamespace),
        ("TZ2M:AAAAAAAAAAAAAAAAAAAAAA", IdentityUnsupportedVersion),
        ("TZ1X:AAAAAAAAAAAAAAAAAAAAAA", IdentityUnsupportedEntityType),
        ("TZ1m:AAAAAAAAAAAAAAAAAAAAAA", IdentityUnsupportedEntityType),
        ("TZ1M:AAAAAAAAAAAAAAAAAAAAAA:extra", IdentityMalformedPayload),
        ("TEZCAN:1:M:00000000-0000-0000-0000-000000000000", IdentityMalformedPayload),
        ("TEZCAN:1:M", IdentityMalformedPayload),
        ("TEZCAN:1:M:00000000-0000-0000-0000-000000000000:extra", IdentityMalformedPayload),
    ),
)
def test_strict_malformed_payloads(payload, error):
    with pytest.raises(error):
        decode_payload(payload)
    with pytest.raises(
        (
            QRMalformedPayload,
            QRWrongNamespace,
            QRUnsupportedVersion,
            QRUnsupportedEntityType,
            QRMalformedUUID,
        )
    ):
        decode_qr_payload(payload)


def test_noncanonical_base64_token_is_rejected():
    # Same 16 bytes as the nil UUID, but unused Base64 bits are non-zero.
    with pytest.raises(IdentityMalformedUUID):
        decode_uuid_token("AAAAAAAAAAAAAAAAAAAAB")
    with pytest.raises(IdentityMalformedUUID):
        decode_payload("TZ1M:AAAAAAAAAAAAAAAAAAAAB")


def test_decoded_token_must_be_exactly_16_bytes(monkeypatch):
    monkeypatch.setattr(
        "identification.codec.base64.urlsafe_b64decode",
        lambda _value: b"\x00" * 15,
    )
    with pytest.raises(IdentityMalformedUUID):
        decode_uuid_token("AAAAAAAAAAAAAAAAAAAAAA")
