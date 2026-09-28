"""
A deterministic sharpness measure for Step 3 - a heuristic, not a judgement.

Edge energy: the variance of a Laplacian-filtered greyscale image, after
scaling to a fixed width so the figure does not depend on the scan's
resolution. A blurred image has soft edges and scores low; printed text and
sharp card edges score high. For a PDF each page is rasterised (150 dpi) and
the lowest page counts, because one unreadable page makes the document
unreadable.

Pillow does the filtering; pypdfium2 rasterises PDFs. Without pypdfium2 a PDF
cannot be measured, and a rule whose input is unknown does not fire - Step 3
records that it was not measured rather than calling it sharp.

The threshold lives in kb/document_quality_rules.csv (QR-13). It was chosen
from the project's own samples: every deliberately blurred file measures about
39, the lowest clear file about 145 (a flat selfie placeholder), text documents
300 and up. It is a heuristic: a very flat, low-detail image (a plain photo)
can score low without being blurred, so a person should see what it refuses.
"""

from pathlib import Path

WIDTH = 1000            # every page is scaled to this width before measuring
PDF_DPI = 150


def _pages(path: Path):
    from PIL import Image
    if path.suffix.lower() == ".pdf":
        try:
            import pypdfium2 as pdfium
        except ImportError:
            return None
        doc = pdfium.PdfDocument(str(path))
        try:
            return [doc[i].render(scale=PDF_DPI / 72).to_pil() for i in range(len(doc))]
        finally:
            doc.close()
    return [Image.open(path)]


def edge_energy(image) -> float:
    from PIL import Image, ImageFilter, ImageStat
    grey = image.convert("L")
    height = max(1, round(grey.height * WIDTH / grey.width))
    grey = grey.resize((WIDTH, height), Image.LANCZOS)
    laplace = ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), scale=1, offset=128)
    return ImageStat.Stat(grey.filter(laplace)).var[0]


def measure(path: str | Path | None) -> float | None:
    """The file's sharpness (lowest page for a PDF), or None if it cannot be
    measured - no file, an unreadable image, or no PDF renderer installed."""
    if not path:
        return None
    path = Path(path)
    if not path.exists():
        return None
    try:
        pages = _pages(path)
        if not pages:
            return None
        return round(min(edge_energy(p) for p in pages), 1)
    except Exception:
        return None
