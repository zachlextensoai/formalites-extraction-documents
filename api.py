"""FastAPI backend for the Document Extraction app."""

import json
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
    allow_origins=["http://localhost:3000"],
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


def load_instructions(doc_type_slug: str) -> str:
    if pv_client:
        instr = pv_client.get_instructions(doc_type_slug, PROMPTVAULT_PROJECT_SLUG)
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
    instructions = load_instructions(doc_type)
    return {
        "fields": [f.model_dump() for f in fields],
        "instructions": instructions,
    }


@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """Upload a PDF and extract text. Returns upload_id and metadata."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    content = await file.read()
    result = extract_text_from_pdf(content)

    upload_id = uuid.uuid4().hex
    pdf_store[upload_id] = {
        "text": result["text"],
        "page_count": result["page_count"],
        "used_ocr": result["used_ocr"],
        "filename": file.filename,
    }

    return {
        "upload_id": upload_id,
        "filename": file.filename,
        "page_count": result["page_count"],
        "used_ocr": result["used_ocr"],
        "truncated": result["truncated"],
    }


@app.post("/api/extract")
def extract(req: ExtractRequest):
    """Run LLM extraction on an uploaded PDF."""
    if not OPENROUTER_API_KEY:
        raise HTTPException(500, "OpenRouter API key not configured")

    pdf_data = pdf_store.get(req.upload_id)
    if not pdf_data:
        raise HTTPException(404, "Upload not found. Please re-upload the PDF.")

    if not pdf_data["text"]:
        raise HTTPException(422, "No text could be extracted from this PDF")

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
