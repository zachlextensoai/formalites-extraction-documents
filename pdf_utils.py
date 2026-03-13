"""PDF text extraction with per-page hybrid strategy.

Phase 1: Extract text per page with PyMuPDF, assess quality per page.
Phase 2: Batch OCR only the pages that need it via ocrmypdf (with deskew,
         denoise, rotation correction, 300 DPI). Falls back to direct
         tesseract + Pillow preprocessing if ocrmypdf fails.
"""

import functools
import io
import logging
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_TEXT_LENGTH = 100_000
OCR_DPI = 300
MIN_TEXT_LENGTH = 30
MIN_PRINTABLE_RATIO = 0.70
MIN_WORD_COUNT = 5
MAX_REPLACEMENT_CHAR_RATIO = 0.05

# Regex for "real words" — 2+ alphabetic chars including French accents
_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ]{2,}")


# ---------------------------------------------------------------------------
# Text quality assessment
# ---------------------------------------------------------------------------

def _assess_text_quality(text: str) -> tuple[bool, str]:
    """Check whether extracted text looks like real readable content.

    Returns (is_good, reason). Conservative: better to OCR unnecessarily
    than to send garbage to the LLM.
    """
    stripped = text.strip()

    # 1. Length gate
    if len(stripped) < MIN_TEXT_LENGTH:
        return False, "too_short"

    # 2. Replacement character ratio (encoding errors)
    replacement_count = stripped.count("\ufffd")
    if replacement_count / len(stripped) > MAX_REPLACEMENT_CHAR_RATIO:
        return False, "encoding_errors"

    # 3. Printable character ratio
    printable = 0
    for ch in stripped:
        cat = unicodedata.category(ch)
        # L=letter, N=number, P=punctuation, Z=separator, S=symbol
        if cat[0] in ("L", "N", "P", "Z", "S"):
            printable += 1
    ratio = printable / len(stripped)
    if ratio < MIN_PRINTABLE_RATIO:
        return False, "garbage_chars"

    # 4. Word-likeness: must contain actual words
    words = _WORD_RE.findall(stripped)
    if len(words) < MIN_WORD_COUNT:
        return False, "no_real_words"

    return True, "ok"


# ---------------------------------------------------------------------------
# PyMuPDF extraction (Phase 1)
# ---------------------------------------------------------------------------

def _extract_text_pymupdf(file_bytes: bytes) -> tuple[list[str], int]:
    """Extract text per page using PyMuPDF (fitz)."""
    import fitz

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    page_texts = [page.get_text().strip() for page in doc]
    page_count = len(doc)
    doc.close()
    return page_texts, page_count


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
        # Check if unpaper is available for the clean option
        has_unpaper = shutil.which("unpaper") is not None

        exit_code = ocrmypdf.ocr(
            inp_path,
            out_path,
            language=[_ocr_lang()],
            pages=pages_str,
            sidecar=sidecar_path,
            force_ocr=True,
            deskew=True,
            clean=has_unpaper,      # requires unpaper
            rotate_pages=True,
            oversample=OCR_DPI,
            optimize=0,             # skip PDF optimization for speed
            jobs=2,
            progress_bar=False,
            output_type="pdf",
        )

        if exit_code not in (
            ocrmypdf.ExitCode.ok,
            ocrmypdf.ExitCode.already_done_ocr,
        ):
            raise RuntimeError(f"ocrmypdf returned exit code {exit_code}")

        # Parse sidecar: ocrmypdf writes text for ALL pages, separated by \x0c
        # Pages not in the OCR set will have empty or minimal text
        sidecar_text = sidecar_path.read_text(encoding="utf-8", errors="replace")
        sidecar_pages = sidecar_text.split("\x0c")

        # Build result list: map sidecar output back to page numbers
        result = [""] * total_pages
        for i, page_num in enumerate(range(1, total_pages + 1)):
            if page_num in page_set and i < len(sidecar_pages):
                result[page_num - 1] = sidecar_pages[i].strip()

        # Optionally return the searchable PDF
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

    for page_num in page_numbers:
        try:
            images = convert_from_bytes(
                file_bytes,
                dpi=OCR_DPI,
                first_page=page_num,
                last_page=page_num,
            )
            if not images:
                continue

            img = images[0]

            # Preprocessing for dirty scans
            img = img.convert("L")                     # grayscale
            img = img.filter(ImageFilter.SHARPEN)       # sharpen
            img = ImageOps.autocontrast(img, cutoff=1)  # improve contrast

            text = pytesseract.image_to_string(img, lang=lang).strip()

            # If still poor, try aggressive binarization
            if not _assess_text_quality(text)[0]:
                binary = img.point(lambda x: 0 if x < 128 else 255, "1")
                text2 = pytesseract.image_to_string(binary, lang=lang).strip()
                if len(text2) > len(text):
                    text = text2

            result[page_num - 1] = text
        except Exception as e:
            logger.warning("Direct tesseract failed for page %d: %s", page_num, e)

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract_text_from_pdf(
    file_bytes: bytes,
    return_searchable_pdf: bool = False,
) -> dict:
    """Extract text from PDF bytes with per-page hybrid strategy.

    Phase 1: PyMuPDF text extraction + quality assessment per page.
    Phase 2: Batch OCR (ocrmypdf or tesseract fallback) for pages that
             failed quality checks.

    Args:
        file_bytes: Raw PDF file content.
        return_searchable_pdf: If True and OCR was performed, include
            'searchable_pdf' key with the OCR'd PDF bytes.

    Returns:
        dict with keys: text, page_count, truncated, used_ocr,
        and optionally: ocr_pages, error, searchable_pdf.
    """
    page_texts: list[str] = []
    page_count = 0

    # ---- Phase 1: PyMuPDF extraction ----
    try:
        page_texts, page_count = _extract_text_pymupdf(file_bytes)
    except Exception as e:
        logger.warning("PyMuPDF extraction failed: %s", e)

    if not page_texts and page_count == 0:
        # PyMuPDF completely failed — try to at least get page count
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                page_count = len(pdf.pages)
                page_texts = [""] * page_count
        except Exception:
            return {
                "text": "",
                "page_count": 0,
                "truncated": False,
                "used_ocr": False,
                "error": "Impossible d'ouvrir ce fichier PDF. Le fichier est peut-être corrompu.",
            }

    # ---- Per-page quality assessment ----
    good_pages: dict[int, str] = {}  # 0-indexed -> text
    bad_page_nums: list[int] = []     # 1-indexed for OCR tools

    for i, text in enumerate(page_texts):
        is_good, reason = _assess_text_quality(text)
        if is_good:
            good_pages[i] = text
        else:
            bad_page_nums.append(i + 1)  # 1-indexed

    # ---- Phase 2: OCR for bad pages ----
    used_ocr = False
    ocr_pages: list[int] = []
    page_errors: list[str] = []
    searchable_pdf: bytes | None = None

    if bad_page_nums:
        has_tesseract = shutil.which("tesseract") is not None

        if not has_tesseract:
            if not good_pages:
                # No text at all and no OCR available
                msg = (
                    f"[Ce PDF contient {page_count} page(s) sans texte exploitable. "
                    f"L'OCR n'est pas disponible (tesseract non installé). "
                    f"Installez : apt-get install tesseract-ocr tesseract-ocr-fra poppler-utils]"
                )
                return {
                    "text": "",
                    "page_count": page_count,
                    "truncated": False,
                    "used_ocr": False,
                    "error": msg,
                }
            else:
                page_errors.append(
                    f"Pages {_compress_page_ranges(bad_page_nums)} : OCR indisponible (tesseract non installé)"
                )
        else:
            # Try ocrmypdf first (superior preprocessing)
            ocr_texts = None
            try:
                ocr_texts, searchable_pdf = _ocr_pages_with_ocrmypdf(
                    file_bytes,
                    bad_page_nums,
                    page_count,
                    return_searchable_pdf=return_searchable_pdf,
                )
            except Exception as e:
                logger.warning("ocrmypdf failed, falling back to direct tesseract: %s", e)

            # Fallback to direct tesseract if ocrmypdf failed
            if ocr_texts is None:
                try:
                    ocr_texts = _ocr_pages_with_tesseract_direct(
                        file_bytes, bad_page_nums, page_count,
                    )
                except Exception as e:
                    logger.warning("Direct tesseract also failed: %s", e)
                    page_errors.append(f"OCR échoué : {e}")

            # Merge OCR results into final page texts
            if ocr_texts:
                for page_num in bad_page_nums:
                    idx = page_num - 1
                    ocr_text = ocr_texts[idx]
                    if ocr_text and _assess_text_quality(ocr_text)[0]:
                        page_texts[idx] = ocr_text
                        ocr_pages.append(page_num)
                        used_ocr = True
                    elif ocr_text:
                        # OCR produced something but quality is poor — use it
                        # anyway as it's better than nothing
                        page_texts[idx] = ocr_text
                        ocr_pages.append(page_num)
                        used_ocr = True
                        page_errors.append(
                            f"Page {page_num} : OCR de qualité limitée"
                        )

    # ---- Build final text with page markers ----
    text_parts = []
    for i, page_text in enumerate(page_texts):
        if page_text.strip():
            text_parts.append(f"--- Page {i + 1} ---\n{page_text}")

    text = "\n\n".join(text_parts)
    truncated = False
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH] + "\n\n[... TRONQUÉ ...]"
        truncated = True

    # Check if we got any usable text at all
    if not text.strip() and page_count > 0:
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

    result = {
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
