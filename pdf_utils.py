import io
import shutil

MAX_TEXT_LENGTH = 100_000


def _extract_text_pymupdf(file_bytes: bytes) -> tuple[list[str], int]:
    """Extract text using PyMuPDF (fitz) — most robust for standard PDFs."""
    import fitz

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    page_texts = [page.get_text().strip() for page in doc]
    page_count = len(doc)
    doc.close()
    return page_texts, page_count


def _extract_text_pdfplumber(file_bytes: bytes) -> tuple[list[str], int]:
    """Extract text using pdfplumber — fallback."""
    import pdfplumber

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        page_texts = [(page.extract_text() or "").strip() for page in pdf.pages]
        return page_texts, len(pdf.pages)


def _ocr_lang() -> str:
    """Detect available tesseract language: prefer French, fallback to English."""
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


def _extract_text_ocr(file_bytes: bytes) -> tuple[list[str], int]:
    """OCR fallback using pdf2image + pytesseract."""
    from pdf2image import convert_from_bytes
    import pytesseract

    lang = _ocr_lang()
    images = convert_from_bytes(file_bytes, dpi=200)  # 200 DPI = good balance speed/quality
    page_texts = [pytesseract.image_to_string(img, lang=lang).strip() for img in images]
    return page_texts, len(images)


def extract_text_from_pdf(file_bytes: bytes) -> dict:
    """Extract text from PDF bytes with page markers.

    Tries PyMuPDF first, then pdfplumber, then OCR as last resort.
    """
    page_texts = []
    page_count = 0
    used_ocr = False

    # Try PyMuPDF first (most robust)
    try:
        page_texts, page_count = _extract_text_pymupdf(file_bytes)
    except Exception:
        pass

    has_text = any(len(t) > 50 for t in page_texts)

    # Fallback to pdfplumber
    if not has_text:
        try:
            page_texts, page_count = _extract_text_pdfplumber(file_bytes)
            has_text = any(len(t) > 50 for t in page_texts)
        except Exception:
            pass

    # Fallback to OCR
    if not has_text and shutil.which("tesseract"):
        try:
            page_texts, page_count = _extract_text_ocr(file_bytes)
            used_ocr = True
        except Exception:
            pass

    # Build text with page markers
    text_parts = []
    for i, page_text in enumerate(page_texts):
        if page_text:
            text_parts.append(f"--- Page {i + 1} ---\n{page_text}")

    text = "\n\n".join(text_parts)
    truncated = False
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH] + "\n\n[... TRONQUÉ ...]"
        truncated = True

    return {
        "text": text,
        "page_count": page_count,
        "truncated": truncated,
        "used_ocr": used_ocr,
    }
