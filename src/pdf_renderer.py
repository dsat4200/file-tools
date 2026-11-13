"""Single-process MuPDF renderer. No Poppler, shell, OCR, or Qt dependency."""
import json
from pathlib import Path
import sys

import pymupdf


def render(source, directory, dpi):
    with pymupdf.open(source) as document:
        if not document.is_pdf:
            raise ValueError("Not a PDF document")
        if document.needs_pass and not document.authenticate(""):
            raise ValueError("This PDF requires a password")
        if not len(document):
            raise ValueError("The PDF contains no pages")
        for number, page in enumerate(document, 1):
            pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
            # Fast, lossless PNG compression: the pixels/resolution are unchanged,
            # but spend less CPU squeezing the last bytes out of each image.
            pixmap.pil_save(Path(directory) / f"page-{number:03d}.png",
                            format="PNG", compress_level=1, optimize=False)
            del pixmap
            print(json.dumps({"event": "page", "page": number, "total": len(document)}), flush=True)


if __name__ == "__main__":
    try:
        render(sys.argv[2], sys.argv[3], int(sys.argv[1]))
    except Exception as error:
        print(f"PDF conversion failed: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
