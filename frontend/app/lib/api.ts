export const API_BASE = "http://localhost:8000";

export interface Field {
  id: string;
  label: string;
  description: string;
}

export interface ExtractionResult {
  field_id: string;
  value: string;
  pdf_reference: string;
  confidence: number;
}

export interface AppConfig {
  doc_types: Record<string, string>;
  models: Record<string, string>;
  has_openrouter: boolean;
  has_promptvault: boolean;
}

export interface UploadResponse {
  upload_id: string;
  filename: string;
  page_count: number;
  used_ocr: boolean;
  truncated: boolean;
}

export async function fetchConfig(): Promise<AppConfig> {
  const res = await fetch(`${API_BASE}/api/config`);
  if (!res.ok) throw new Error("Failed to fetch config");
  return res.json();
}

export async function fetchFields(
  docType: string
): Promise<{ fields: Field[]; instructions: string }> {
  const res = await fetch(`${API_BASE}/api/fields/${docType}`);
  if (!res.ok) throw new Error("Failed to fetch fields");
  return res.json();
}

export async function uploadPdf(file: File): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/api/upload`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Upload failed" }));
    throw new Error(err.detail || "Upload failed");
  }
  return res.json();
}

export async function runExtraction(params: {
  upload_id: string;
  doc_type: string;
  model: string;
  fields: Field[];
  instructions: string;
}): Promise<{ results: ExtractionResult[] }> {
  const res = await fetch(`${API_BASE}/api/extract`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Extraction failed" }));
    throw new Error(err.detail || "Extraction failed");
  }
  return res.json();
}

export async function saveFields(params: {
  doc_type: string;
  fields: Field[];
  instructions: string;
}): Promise<void> {
  const res = await fetch(`${API_BASE}/api/fields/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Save failed" }));
    throw new Error(err.detail || "Save failed");
  }
}

export async function saveInstructions(params: {
  instructions: string;
}): Promise<void> {
  const res = await fetch(`${API_BASE}/api/instructions/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const err = await res
      .json()
      .catch(() => ({ detail: "Save instructions failed" }));
    throw new Error(err.detail || "Save instructions failed");
  }
}
