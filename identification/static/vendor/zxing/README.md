# Vendored ZXing browser scanner

- `@zxing/browser` 0.2.1
- bundled peer: `@zxing/library` 0.23.0
- source: <https://github.com/zxing-js/browser>
- runtime network access: none

The checked-in UMD distribution is used directly by the Django scanner page
via `BrowserMultiFormatReader`, constrained to QR Code and Code 128.
DataMatrix is not a V1 label carrier. The project does not require Node.js
or a frontend build step at runtime. Licenses are stored beside the
distribution file.
