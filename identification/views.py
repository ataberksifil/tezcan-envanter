from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.views import View
from django.views.generic import DetailView, TemplateView

from catalog.models import Material
from identification.codec import (
    IdentityCodecError,
    IdentityEntityType,
    decode_payload,
    encode_location,
    encode_material,
    encode_serialized_asset,
)
from identification.forms import ResolveForm
from identification.rendering import generate_code128_svg, generate_qr_png
from identification.resolver import IdentityObjectNotFound, resolve_identity
from inventory.models import SerializedAsset
from locations.models import Location

VIEW_PERMISSION_BY_TYPE = {
    IdentityEntityType.MATERIAL: "catalog.view_material",
    IdentityEntityType.SERIALIZED_ASSET: "inventory.view_stockbalance",
    IdentityEntityType.LOCATION: "locations.view_location",
}
SCANNER_VIEW_PERMISSIONS = tuple(VIEW_PERMISSION_BY_TYPE.values())
ENCODER_BY_TYPE = {
    IdentityEntityType.MATERIAL: encode_material,
    IdentityEntityType.SERIALIZED_ASSET: encode_serialized_asset,
    IdentityEntityType.LOCATION: encode_location,
}


class LabelProfile(StrEnum):
    STANDARD = "standard"
    COMPACT = "compact"


def _scanner_access_allowed(user) -> bool:
    return user.is_authenticated and any(
        user.has_perm(permission) for permission in SCANNER_VIEW_PERMISSIONS
    )


def _target_url(entity_type: IdentityEntityType, object_id) -> str:
    route_by_type = {
        IdentityEntityType.MATERIAL: "catalog:material-detail",
        IdentityEntityType.SERIALIZED_ASSET: "inventory:serialized-asset-detail",
        IdentityEntityType.LOCATION: "locations:location-detail",
    }
    return reverse(route_by_type[entity_type], args=[object_id])


def _encode_payload(entity_type: IdentityEntityType, object_id) -> str:
    return ENCODER_BY_TYPE[entity_type](object_id)


def _render_scanner(request, *, form, error_message=None, status=200):
    return render(
        request,
        "identification/scanner.html",
        {"form": form, "scan_error": error_message},
        status=status,
    )


class ScannerAccessMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not _scanner_access_allowed(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


QRScannerAccessMixin = ScannerAccessMixin


class ScannerView(ScannerAccessMixin, TemplateView):
    template_name = "identification/scanner.html"
    http_method_names = ["get", "head"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = ResolveForm()
        return context


QRScannerView = ScannerView


class ResolveView(ScannerAccessMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        form = ResolveForm(request.POST)
        if not form.is_valid():
            return _render_scanner(request, form=form, status=400)

        try:
            identity = decode_payload(form.cleaned_data["payload"])
        except IdentityCodecError as exc:
            form.add_error("payload", exc.user_message)
            return _render_scanner(
                request,
                form=form,
                error_message=exc.user_message,
                status=400,
            )

        required_permission = VIEW_PERMISSION_BY_TYPE[identity.entity_type]
        if not request.user.has_perm(required_permission):
            raise PermissionDenied

        try:
            resolved = resolve_identity(identity)
        except IdentityObjectNotFound as exc:
            form.add_error("payload", exc.user_message)
            return _render_scanner(
                request,
                form=form,
                error_message=exc.user_message,
                status=404,
            )

        return redirect(_target_url(identity.entity_type, resolved.target.pk))


QRResolveView = ResolveView


class _IdentityImageView(LoginRequiredMixin, PermissionRequiredMixin, View):
    model = None
    entity_type: ClassVar[IdentityEntityType]
    filename_prefix: ClassVar[str]
    content_type: ClassVar[str]
    file_extension: ClassVar[str]
    http_method_names = ["get", "head"]

    def get_payload(self, target) -> str:
        return _encode_payload(self.entity_type, target.pk)

    def render_payload(self, payload: str) -> bytes:
        raise NotImplementedError

    def get(self, request, pk):
        target = get_object_or_404(self.model, pk=pk)
        response = HttpResponse(
            self.render_payload(self.get_payload(target)),
            content_type=self.content_type,
        )
        response["X-Content-Type-Options"] = "nosniff"
        disposition = "attachment" if request.GET.get("download") == "1" else "inline"
        response["Content-Disposition"] = (
            f'{disposition}; filename="{self.filename_prefix}-{target.pk}.{self.file_extension}"'
        )
        patch_cache_control(response, private=True, no_store=True)
        return response


class QRImageView(_IdentityImageView):
    content_type = "image/png"
    file_extension = "png"

    def render_payload(self, payload: str) -> bytes:
        return generate_qr_png(payload)


class Code128ImageView(_IdentityImageView):
    content_type = "image/svg+xml"
    file_extension = "svg"

    def render_payload(self, payload: str) -> bytes:
        return generate_code128_svg(payload)


class MaterialQRImageView(QRImageView):
    permission_required = "catalog.view_material"
    model = Material
    entity_type = IdentityEntityType.MATERIAL
    filename_prefix = "material-qr"


class SerializedAssetQRImageView(QRImageView):
    permission_required = "inventory.view_stockbalance"
    model = SerializedAsset
    entity_type = IdentityEntityType.SERIALIZED_ASSET
    filename_prefix = "asset-qr"


class LocationQRImageView(QRImageView):
    permission_required = "locations.view_location"
    model = Location
    entity_type = IdentityEntityType.LOCATION
    filename_prefix = "location-qr"


class MaterialCode128ImageView(Code128ImageView):
    permission_required = "catalog.view_material"
    model = Material
    entity_type = IdentityEntityType.MATERIAL
    filename_prefix = "material-code128"


class SerializedAssetCode128ImageView(Code128ImageView):
    permission_required = "inventory.view_stockbalance"
    model = SerializedAsset
    entity_type = IdentityEntityType.SERIALIZED_ASSET
    filename_prefix = "asset-code128"


class LocationCode128ImageView(Code128ImageView):
    permission_required = "locations.view_location"
    model = Location
    entity_type = IdentityEntityType.LOCATION
    filename_prefix = "location-code128"


class LabelView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    template_name = "identification/label.html"
    context_object_name = "target"
    entity_type: ClassVar[IdentityEntityType]
    qr_image_route: ClassVar[str]
    code128_image_route: ClassVar[str]
    standard_label_route: ClassVar[str]
    compact_label_route: ClassVar[str]
    http_method_names = ["get", "head"]

    def get_profile(self) -> LabelProfile:
        requested = self.kwargs.get("profile", LabelProfile.STANDARD)
        try:
            return LabelProfile(requested)
        except ValueError:
            return LabelProfile.STANDARD

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.get_profile()
        payload = _encode_payload(self.entity_type, self.object.pk)
        context.update(
            {
                "entity_type": self.entity_type,
                "label_profile": profile,
                "is_standard_label": profile == LabelProfile.STANDARD,
                "is_compact_label": profile == LabelProfile.COMPACT,
                "identity_payload": payload,
                "qr_payload": payload,
                "qr_image_url": reverse(self.qr_image_route, args=[self.object.pk]),
                "code128_image_url": reverse(
                    self.code128_image_route, args=[self.object.pk]
                ),
                "standard_label_url": reverse(
                    self.standard_label_route, args=[self.object.pk]
                ),
                "compact_label_url": reverse(
                    self.compact_label_route, args=[self.object.pk]
                ),
            }
        )
        return context


QRLabelView = LabelView


class MaterialLabelView(LabelView):
    permission_required = "catalog.view_material"
    model = Material
    entity_type = IdentityEntityType.MATERIAL
    qr_image_route = "identification:material-image"
    code128_image_route = "identification:material-code128"
    standard_label_route = "identification:material-label"
    compact_label_route = "identification:material-label-compact"


class SerializedAssetLabelView(LabelView):
    permission_required = "inventory.view_stockbalance"
    model = SerializedAsset
    entity_type = IdentityEntityType.SERIALIZED_ASSET
    qr_image_route = "identification:asset-image"
    code128_image_route = "identification:asset-code128"
    standard_label_route = "identification:asset-label"
    compact_label_route = "identification:asset-label-compact"

    def get_queryset(self):
        return SerializedAsset.objects.select_related("material")


class LocationLabelView(LabelView):
    permission_required = "locations.view_location"
    model = Location
    entity_type = IdentityEntityType.LOCATION
    qr_image_route = "identification:location-image"
    code128_image_route = "identification:location-code128"
    standard_label_route = "identification:location-label"
    compact_label_route = "identification:location-label-compact"


MaterialQRLabelView = MaterialLabelView
SerializedAssetQRLabelView = SerializedAssetLabelView
LocationQRLabelView = LocationLabelView
