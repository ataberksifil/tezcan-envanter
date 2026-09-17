"""Read-only identity target resolution.

Carrier is already gone by this point: the codec yields a canonical identity,
and this module resolves that identity to one domain object.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from catalog.models import Material
from inventory.models import SerializedAsset
from locations.models import Location

from identification.codec import CanonicalIdentity, IdentityEntityType, QRIdentity

IdentityTarget: TypeAlias = Material | SerializedAsset | Location
QRTarget = IdentityTarget


class IdentityObjectNotFound(LookupError):
    code = "object_not_found"
    user_message = "Kodun işaret ettiği kayıt bulunamadı."


QRObjectNotFound = IdentityObjectNotFound


@dataclass(frozen=True, slots=True)
class ResolvedIdentity:
    identity: CanonicalIdentity
    target: IdentityTarget


ResolvedQRIdentity = ResolvedIdentity


def resolve_identity(identity: CanonicalIdentity) -> ResolvedIdentity:
    model_by_type = {
        IdentityEntityType.MATERIAL: Material,
        IdentityEntityType.SERIALIZED_ASSET: SerializedAsset,
        IdentityEntityType.LOCATION: Location,
    }
    model = model_by_type[identity.entity_type]
    try:
        target = model.objects.get(pk=identity.object_id)
    except model.DoesNotExist:
        raise IdentityObjectNotFound from None
    return ResolvedIdentity(identity=identity, target=target)


def resolve_qr_identity(identity: QRIdentity) -> ResolvedIdentity:
    return resolve_identity(identity)
