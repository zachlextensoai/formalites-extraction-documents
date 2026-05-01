"""PDF text extraction — always-OCR pipeline.

Embedded text layers (especially Docusign-produced ones) can be silently
corrupt: identical-looking glyphs swapped, dates mistyped, etc. We don't
trust them. Every PDF is re-OCR'd via ocrmypdf (preferred, with deskew /
rotate / 150 DPI) and falls back to direct tesseract + Pillow if ocrmypdf
errors out. The original embedded text layer is only used as a last resort
if no OCR path is available.
"""

import functools
import io
import logging
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_TEXT_LENGTH = 100_000
OCR_DPI = 150
MIN_TEXT_LENGTH = 30
# Cap parallel OCR jobs: each ocrmypdf worker / tesseract subprocess holds a
# rasterized page in memory, so unbounded parallelism on big PDFs can
# exhaust RAM. 8 is a safe ceiling for typical laptops/servers.
MAX_OCR_JOBS = 8


def _job_count(page_count: int) -> int:
    """Pick a parallelism level: bounded by CPUs, page count, and MAX_OCR_JOBS."""
    return max(1, min(os.cpu_count() or 2, page_count, MAX_OCR_JOBS))


# ---------------------------------------------------------------------------
# PyMuPDF text extraction (used for page-count + last-resort fallback)
# ---------------------------------------------------------------------------

def _extract_text_pymupdf(file_bytes: bytes) -> tuple[list[str], int]:
    """Extract text per page using PyMuPDF (fitz)."""
    import fitz

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    page_texts = [page.get_text().strip() for page in doc]
    page_count = len(doc)
    doc.close()
    return page_texts, page_count


def _page_count_only(file_bytes: bytes) -> int:
    """Get the page count without extracting text. Returns 0 on failure."""
    try:
        import fitz
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        n = len(doc)
        doc.close()
        return n
    except Exception:
        pass
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            return len(pdf.pages)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# OCR language detection (cached)
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _ocr_lang() -> str:
    """Detect available tesseract language: prefer French, fallback English."""
    import subprocess

    try:
        result = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True, text=True, timeout=5,
        )
        if "fra" in result.stdout:
            return "fra"
    except Exception:
        pass
    return "eng"


# ---------------------------------------------------------------------------
# Page range compression utility
# ---------------------------------------------------------------------------

def _compress_page_ranges(pages: list[int]) -> str:
    """Convert [1,2,3,7,8,12] to '1-3,7-8,12' for ocrmypdf pages param."""
    if not pages:
        return ""
    sorted_pages = sorted(pages)
    ranges = []
    start = end = sorted_pages[0]
    for p in sorted_pages[1:]:
        if p == end + 1:
            end = p
        else:
            ranges.append(f"{start}-{end}" if start != end else str(start))
            start = end = p
    ranges.append(f"{start}-{end}" if start != end else str(start))
    return ",".join(ranges)


# ---------------------------------------------------------------------------
# ocrmypdf-based OCR (primary OCR method)
# ---------------------------------------------------------------------------

def _ocr_pages_with_ocrmypdf(
    file_bytes: bytes,
    page_numbers: list[int],
    total_pages: int,
    return_searchable_pdf: bool = False,
) -> tuple[list[str], bytes | None]:
    """OCR specific pages using ocrmypdf with deskew/denoise/rotation.

    Args:
        file_bytes: Raw PDF bytes.
        page_numbers: 1-indexed page numbers to OCR.
        total_pages: Total number of pages in the PDF.
        return_searchable_pdf: If True, return the OCR'd PDF bytes.

    Returns:
        (page_texts, searchable_pdf_bytes_or_None)
        page_texts has total_pages entries; non-OCR'd pages are empty strings.
    """
    import ocrmypdf

    pages_str = _compress_page_ranges(page_numbers)
    page_set = set(page_numbers)

    with (
        tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp,
        tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as sidecar,
        tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as out,
    ):
        inp.write(file_bytes)
        inp_path = Path(inp.name)
        sidecar_path = Path(sidecar.name)
        out_path = Path(out.name)

    try:
        has_unpaper = shutil.which("unpaper") is not None

        exit_code = ocrmypdf.ocr(
            inp_path,
            out_path,
            language=[_ocr_lang()],
            pages=pages_str,
            sidecar=sidecar_path,
            force_ocr=True,
            deskew=True,
            clean=has_unpaper,
            rotate_pages=True,
            oversample=OCR_DPI,
            optimize=0,             # skip PDF optimization for speed
            jobs=_job_count(total_pages),
            progress_bar=False,
            output_type="pdf",
            # Docusign-signed PDFs would otherwise raise DigitalSignatureError;
            # we never redistribute the produced PDF, only mine its text.
            invalidate_digital_signatures=True,
        )

        if exit_code not in (
            ocrmypdf.ExitCode.ok,
            ocrmypdf.ExitCode.already_done_ocr,
        ):
            raise RuntimeError(f"ocrmypdf returned exit code {exit_code}")

        sidecar_text = sidecar_path.read_text(encoding="utf-8", errors="replace")
        sidecar_pages = sidecar_text.split("\x0c")

        result = [""] * total_pages
        for i, page_num in enumerate(range(1, total_pages + 1)):
            if page_num in page_set and i < len(sidecar_pages):
                result[page_num - 1] = sidecar_pages[i].strip()

        searchable_pdf = None
        if return_searchable_pdf and out_path.exists():
            searchable_pdf = out_path.read_bytes()

        return result, searchable_pdf

    finally:
        inp_path.unlink(missing_ok=True)
        sidecar_path.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Direct tesseract + Pillow fallback
# ---------------------------------------------------------------------------

def _ocr_pages_with_tesseract_direct(
    file_bytes: bytes,
    page_numbers: list[int],
    total_pages: int,
) -> list[str]:
    """Last-resort OCR: pdf2image + Pillow preprocessing + pytesseract.

    Args:
        page_numbers: 1-indexed page numbers to OCR.
        total_pages: Total pages in the PDF.

    Returns:
        List of total_pages entries; non-OCR'd pages are empty strings.
    """
    from pdf2image import convert_from_bytes
    import pytesseract
    from PIL import ImageFilter, ImageOps

    lang = _ocr_lang()
    result = [""] * total_pages

    def _ocr_one(page_num: int) -> tuple[int, str]:
        try:
            images = convert_from_bytes(
                file_bytes,
                dpi=OCR_DPI,
                first_page=page_num,
                last_page=page_num,
            )
            if not images:
                return page_num, ""

            img = images[0]
            img = img.convert("L")
            img = img.filter(ImageFilter.SHARPEN)
            img = ImageOps.autocontrast(img, cutoff=1)

            text = pytesseract.image_to_string(img, lang=lang).strip()

            # If the first pass returns very little, retry on a binarized
            # image (helps on scans with weak contrast).
            if len(text) < MIN_TEXT_LENGTH:
                binary = img.point(lambda x: 0 if x < 128 else 255, "1")
                text2 = pytesseract.image_to_string(binary, lang=lang).strip()
                if len(text2) > len(text):
                    text = text2

            return page_num, text
        except Exception as e:
            logger.warning("Direct tesseract failed for page %d: %s", page_num, e)
            return page_num, ""

    workers = _job_count(len(page_numbers))
    if workers <= 1 or len(page_numbers) <= 1:
        for page_num in page_numbers:
            _, text = _ocr_one(page_num)
            result[page_num - 1] = text
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for page_num, text in ex.map(_ocr_one, page_numbers):
                result[page_num - 1] = text

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract_text_from_pdf(
    file_bytes: bytes,
    return_searchable_pdf: bool = False,
) -> dict:
    """Extract text from PDF bytes by OCR'ing every page.

    Pipeline:
      1. Page count via PyMuPDF (or pdfplumber).
      2. OCR all pages with ocrmypdf (preferred).
      3. If ocrmypdf fails, OCR via direct tesseract + Pillow.
      4. If both OCR paths fail, fall back to the embedded text layer
         (PyMuPDF) — better than nothing, but flagged via page_errors.

    Args:
        file_bytes: Raw PDF file content.
        return_searchable_pdf: If True and ocrmypdf succeeded, include
            'searchable_pdf' key with the OCR'd PDF bytes.

    Returns:
        dict with keys: text, page_count, truncated, used_ocr,
        and optionally: ocr_pages, error, page_errors, searchable_pdf.
    """
    page_count = _page_count_only(file_bytes)

    if page_count == 0:
        return {
            "text": "",
            "page_count": 0,
            "truncated": False,
            "used_ocr": False,
            "error": "Impossible d'ouvrir ce fichier PDF. Le fichier est peut-être corrompu.",
        }

    all_pages = list(range(1, page_count + 1))
    has_tesseract = shutil.which("tesseract") is not None
    page_texts: list[str] = [""] * page_count
    used_ocr = False
    ocr_pages: list[int] = []
    searchable_pdf: bytes | None = None
    page_errors: list[str] = []

    if has_tesseract:
        # Primary: ocrmypdf
        try:
            page_texts, searchable_pdf = _ocr_pages_with_ocrmypdf(
                file_bytes,
                all_pages,
                page_count,
                return_searchable_pdf=return_searchable_pdf,
            )
            if any(t.strip() for t in page_texts):
                used_ocr = True
                ocr_pages = [i for i, t in enumerate(page_texts, 1) if t.strip()]
        except Exception as e:
            logger.warning("ocrmypdf failed, falling back to direct tesseract: %s", e)
            page_errors.append(f"ocrmypdf indisponible : {e}")

        # Fallback: direct tesseract
        if not used_ocr:
            try:
                page_texts = _ocr_pages_with_tesseract_direct(
                    file_bytes, all_pages, page_count,
                )
                if any(t.strip() for t in page_texts):
                    used_ocr = True
                    ocr_pages = [i for i, t in enumerate(page_texts, 1) if t.strip()]
            except Exception as e:
                logger.warning("Direct tesseract also failed: %s", e)
                page_errors.append(f"OCR tesseract échoué : {e}")
    else:
        page_errors.append(
            "OCR indisponible (tesseract non installé). "
            "Installez : apt-get install tesseract-ocr tesseract-ocr-fra poppler-utils"
        )

    # Last-resort fallback: read the embedded text layer. Known to be
    # unreliable for Docusign-signed PDFs, but better than empty output.
    if not used_ocr:
        try:
            embedded, _ = _extract_text_pymupdf(file_bytes)
            if any(t.strip() for t in embedded):
                page_texts = embedded
                page_errors.append(
                    "OCR indisponible — couche de texte intégrée utilisée (peut être inexacte)."
                )
        except Exception as e:
            logger.warning("PyMuPDF fallback also failed: %s", e)

    # Stitch pages with markers
    text_parts = [
        f"--- Page {i + 1} ---\n{t}"
        for i, t in enumerate(page_texts)
        if t.strip()
    ]
    text = "\n\n".join(text_parts)

    truncated = False
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH] + "\n\n[... TRONQUÉ ...]"
        truncated = True

    if not text.strip():
        msg = f"[Aucun texte exploitable extrait de ce PDF ({page_count} pages)."
        if page_errors:
            msg += " " + " | ".join(page_errors)
        msg += "]"
        return {
            "text": "",
            "page_count": page_count,
            "truncated": False,
            "used_ocr": used_ocr,
            "ocr_pages": ocr_pages,
            "error": msg,
        }

    result: dict = {
        "text": text,
        "page_count": page_count,
        "truncated": truncated,
        "used_ocr": used_ocr,
        "ocr_pages": ocr_pages,
    }

    if page_errors:
        result["page_errors"] = page_errors

    if searchable_pdf:
        result["searchable_pdf"] = searchable_pdf

    return result
