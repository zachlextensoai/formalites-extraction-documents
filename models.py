import uuid
from pydantic import BaseModel, Field


class FieldDefinition(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    label: str
    description: str


class ExtractionResult(BaseModel):
    field_id: str
    value: str = ""
    pdf_reference: str = ""
    confidence: float = 0.0
