"""Server-side carrier renderers for the same canonical identity payload."""

from __future__ import annotations

from io import BytesIO

import barcode
from barcode.writer import SVGWriter
import qrcode
from qrcode.constants import ERROR_CORRECT_M

CODE128_MODULE_WIDTH_MM = 0.25
CODE128_MODULE_HEIGHT_MM = 15.0
CODE128_QUIET_ZONE_MM = 2.5
CODE128_WRITER_OPTIONS = {
    "module_width": CODE128_MODULE_WIDTH_MM,
    "module_height": CODE128_MODULE_HEIGHT_MM,
    "quiet_zone": CODE128_QUIET_ZONE_MM,
    "font_size": 0,
    "text_distance": 1,
    "write_text": False,
}


def generate_qr_png(payload: str) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def generate_code128_svg(payload: str) -> bytes:
    if not isinstance(payload, str) or not payload:
        raise ValueError("payload is required")
    output = BytesIO()
    barcode.get_barcode_class("code128")(payload, writer=SVGWriter()).write(
        output,
        options=CODE128_WRITER_OPTIONS,
    )
    return output.getvalue()
