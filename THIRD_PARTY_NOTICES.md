# Third-party components

Application source: MIT, see LICENSE. The portable Windows distribution also includes:

- CPython 3.14.10: Python Software Foundation License and bundled component notices. See runtime/LICENSE.txt and the official Python distribution.
- Pillow 12.3.0: HPND and bundled library licenses, retained in vendor/pillow-12.3.0.dist-info/licenses.
- pypdfium2 5.14.0 and its bundled PDFium: Apache-2.0 / BSD-style notices and third-party licenses, retained in the wheel's vendor directories.

Exact download URLs, versions and SHA-256 hashes are in tools/distribution-lock.json. The build preserves original runtime and wheel license files. No model weights or proprietary fonts are redistributed. Fictional sample images may be rasterized with a font already installed on the build machine; that font file is not included.
