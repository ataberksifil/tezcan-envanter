from __future__ import annotations

import uuid
from io import BytesIO
from xml.etree import ElementTree as ET

from PIL import Image

from identification.codec import encode_location, encode_material, encode_serialized_asset
from identification.rendering import generate_code128_svg, generate_qr_png

WORKSHOP_LABEL_WIDTH_MM = 100
CODE128_MIN_PRACTICAL_WIDTH_MM = 70


def _svg_width_mm(svg: bytes) -> float:
    root = ET.fromstring(svg)
    width = root.attrib["width"]
    assert width.endswith("mm")
    return float(width[:-2])


def test_qr_and_code128_encode_the_same_canonical_payload():
    payloads = (
        encode_material(uuid.uuid4()),
        encode_serialized_asset(uuid.uuid4()),
        encode_location(uuid.uuid4()),
    )
    for payload in payloads:
        assert payload.startswith("TZ1")
        assert len(payload) == 27
        qr_png = generate_qr_png(payload)
        code128_svg = generate_code128_svg(payload)
        image = Image.open(BytesIO(qr_png))
        assert image.format == "PNG"
        assert image.width == image.height
        assert code128_svg.startswith(b"<?xml")
        assert b"<svg" in code128_svg
        assert b"DataMatrix" not in code128_svg
        width_mm = _svg_width_mm(code128_svg)
        assert width_mm <= WORKSHOP_LABEL_WIDTH_MM
        assert width_mm >= CODE128_MIN_PRACTICAL_WIDTH_MM


def test_code128_compact_payload_fits_100mm_workshop_label():
    payloads = {
        "material": encode_material(uuid.UUID("00000000-0000-0000-0000-000000000000")),
        "asset": encode_serialized_asset(
            uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
        ),
        "location": encode_location(uuid.UUID("18a8df7e-d5b8-4ebb-93d7-47d914b90db0")),
    }
    measured = {}
    for name, payload in payloads.items():
        assert payload.startswith(("TZ1M:", "TZ1A:", "TZ1L:"))
        width_mm = _svg_width_mm(generate_code128_svg(payload))
        measured[name] = width_mm
        assert width_mm <= WORKSHOP_LABEL_WIDTH_MM
        assert width_mm > 50
    assert measured["material"] == measured["asset"] == measured["location"] == 88.0
