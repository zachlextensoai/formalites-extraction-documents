"""FastAPI backend for the Document Extraction app."""

import io
import json
import uuid
from pathlib import Path

import subprocess
import tempfile

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from config import (
    OPENROUTER_API_KEY,
    PROMPTVAULT_API_KEY,
    PROMPTVAULT_BASE_URL,
    PROMPTVAULT_PROJECT_SLUG,
    AVAILABLE_MODELS,
    DOC_TYPES,
)
from models import FieldDefinition, ExtractionResult
from pdf_utils import extract_text_from_pdf
from promptvault_client import PromptVaultClient
from extraction import extract_fields, DEFAULT_INSTRUCTIONS

app = FastAPI(title="Extracteur de Documents API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

pv_client = PromptVaultClient(PROMPTVAULT_BASE_URL, PROMPTVAULT_API_KEY) if PROMPTVAULT_API_KEY else None

# In-memory store for uploaded PDF text (per session)
pdf_store: dict[str, dict] = {}


# --- Helpers ---
def load_default_fields(doc_type_slug: str) -> list[FieldDefinition]:
    path = Path(__file__).parent / "default_fields" / f"{doc_type_slug.replace('-', '_')}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return [FieldDefinition(**f) for f in data.get("fields", [])]
    return []


def load_fields(doc_type_slug: str) -> list[FieldDefinition]:
    if pv_client:
        fields = pv_client.get_fields(doc_type_slug, PROMPTVAULT_PROJECT_SLUG)
        if fields:
            return fields
    return load_default_fields(doc_type_slug)


def load_instructions() -> str:
    if pv_client:
        instr = pv_client.get_instructions(PROMPTVAULT_PROJECT_SLUG)
        if instr:
            return instr
    return DEFAULT_INSTRUCTIONS


# --- Request/Response models ---
class ExtractRequest(BaseModel):
    upload_id: str
    doc_type: str
    model: str
    fields: list[FieldDefinition]
    instructions: str


class SaveFieldsRequest(BaseModel):
    doc_type: str
    fields: list[FieldDefinition]
    instructions: str


class SaveInstructionsRequest(BaseModel):
    instructions: str


# --- Endpoints ---
@app.get("/api/config")
def get_config():
    """Return available doc types, models, and feature flags."""
    return {
        "doc_types": DOC_TYPES,
        "models": AVAILABLE_MODELS,
        "has_openrouter": bool(OPENROUTER_API_KEY),
        "has_promptvault": bool(pv_client),
    }


@app.get("/api/fields/{doc_type}")
def get_fields(doc_type: str):
    """Get field definitions and instructions for a document type."""
    fields = load_fields(doc_type)
    instructions = load_instructions()
    return {
        "fields": [f.model_dump() for f in fields],
        "instructions": instructions,
    }


def _quick_page_count(file_bytes: bytes) -> int:
    """Get page count quickly without full text extraction."""
    try:
        import fitz
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        count = len(doc)
        doc.close()
        return count
    except Exception:
        pass
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            return len(pdf.pages)
    except Exception:
        return 0


@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """Upload a PDF. Text extraction is deferred to /api/extract for speed."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    content = await file.read()
    page_count = _quick_page_count(content)
    upload_id = uuid.uuid4().hex

    # Store raw bytes — text extraction happens lazily on first extract call
    pdf_store[upload_id] = {
        "raw_bytes": content,
        "text": None,  # extracted lazily
        "page_count": page_count,
        "used_ocr": False,
        "filename": file.filename,
    }

    return {
        "upload_id": upload_id,
        "filename": file.filename,
        "page_count": page_count,
        "used_ocr": False,
        "truncated": False,
    }


def _ensure_text_extracted(pdf_data: dict) -> None:
    """Lazily extract text from stored PDF bytes (only once)."""
    if pdf_data["text"] is not None:
        return
    raw = pdf_data.get("raw_bytes")
    if not raw:
        pdf_data["text"] = ""
        return
    result = extract_text_from_pdf(raw)
    pdf_data["text"] = result["text"]
    pdf_data["page_count"] = result["page_count"]
    pdf_data["used_ocr"] = result["used_ocr"]
    pdf_data["extraction_error"] = result.get("error")

    # If OCR was needed, create a searchable PDF with text layer for the viewer
    if result["used_ocr"]:
        pdf_data["searchable_pdf"] = _create_searchable_pdf(raw)


def _create_searchable_pdf(raw_bytes: bytes) -> bytes | None:
    """Use ocrmypdf to add an invisible text layer to a scanned PDF."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as inp:
            inp.write(raw_bytes)
            inp_path = inp.name
        out_path = inp_path.replace(".pdf", "_ocr.pdf")
        subprocess.run(
            ["ocrmypdf", "--skip-text", "-l", "fra", "--jobs", "2",
             "--optimize", "0", inp_path, out_path],
            capture_output=True, timeout=120,
        )
        if Path(out_path).exists():
            result = Path(out_path).read_bytes()
            Path(inp_path).unlink(missing_ok=True)
            Path(out_path).unlink(missing_ok=True)
            return result
    except Exception:
        pass
    return None


@app.get("/api/pdf/{upload_id}")
def get_pdf(upload_id: str):
    """Serve the PDF file (searchable OCR version if available)."""
    pdf_data = pdf_store.get(upload_id)
    if not pdf_data:
        raise HTTPException(404, "Upload not found")
    # Prefer searchable OCR version, fall back to original
    content = pdf_data.get("searchable_pdf") or pdf_data.get("raw_bytes")
    if not content:
        raise HTTPException(404, "PDF data not found")
    filename = pdf_data.get("filename", "document.pdf")
    ascii_filename = filename.encode("ascii", "ignore").decode("ascii") or "document.pdf"
    from urllib.parse import quote
    utf8_filename = quote(filename)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=\"{ascii_filename}\"; filename*=UTF-8''{utf8_filename}"
        },
    )


@app.post("/api/extract")
def extract(req: ExtractRequest):
    """Run LLM extraction on an uploaded PDF."""
    if not OPENROUTER_API_KEY:
        raise HTTPException(500, "OpenRouter API key not configured")

    pdf_data = pdf_store.get(req.upload_id)
    if not pdf_data:
        raise HTTPException(404, "Upload not found. Please re-upload the PDF.")

    # Lazy text extraction (may OCR — can take a while for scanned PDFs)
    _ensure_text_extracted(pdf_data)

    if not pdf_data["text"]:
        error_detail = pdf_data.get("extraction_error") or "No text could be extracted from this PDF"
        raise HTTPException(422, error_detail)

    model_id = None
    for label, mid in AVAILABLE_MODELS.items():
        if mid == req.model or label == req.model:
            model_id = mid
            break
    if not model_id:
        model_id = req.model

    results = extract_fields(
        pdf_data["text"],
        req.fields,
        OPENROUTER_API_KEY,
        model_id,
        instructions=req.instructions,
    )
    return {"results": [r.model_dump() for r in results]}


@app.post("/api/fields/save")
def save_fields(req: SaveFieldsRequest):
    """Save field definitions to PromptVault."""
    if not pv_client:
        raise HTTPException(500, "PromptVault not configured")

    # Find the label for this doc type
    doc_type_label = ""
    for label, slug in DOC_TYPES.items():
        if slug == req.doc_type:
            doc_type_label = label
            break

    ok = pv_client.save_fields(
        req.doc_type,
        PROMPTVAULT_PROJECT_SLUG,
        req.fields,
        doc_type_label=doc_type_label,
        instructions=req.instructions,
    )
    if ok:
        return {"status": "ok"}
    raise HTTPException(500, "Failed to save fields")


@app.post("/api/instructions/save")
def save_instructions(req: SaveInstructionsRequest):
    """Save extraction instructions to their dedicated PromptVault prompt."""
    if not pv_client:
        raise HTTPException(500, "PromptVault not configured")

    ok = pv_client.save_instructions(
        PROMPTVAULT_PROJECT_SLUG,
        req.instructions,
    )
    if ok:
        return {"status": "ok"}
    raise HTTPException(500, "Failed to save instructions")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
