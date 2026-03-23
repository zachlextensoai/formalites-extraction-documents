"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import dynamic from "next/dynamic";
import {
  fetchConfig,
  fetchFields,
  uploadPdf,
  runExtraction,
  saveFields,
  saveInstructions,
  API_BASE,
  type Field,
  type ExtractionResult,
  type AppConfig,
  type UploadResponse,
} from "./lib/api";

const PdfViewer = dynamic(() => import("./components/PdfViewer"), {
  ssr: false,
  loading: () => (
    <div className="flex h-96 items-center justify-center text-gray-400">
      Chargement du viewer PDF...
    </div>
  ),
});

function parsePageNumber(ref: string): number | null {
  const m = ref.match(/[Pp]age\s*(\d+)/);
  return m ? parseInt(m[1], 10) : null;
}

// Toast notification component
function Toast({
  message,
  type,
  onClose,
}: {
  message: string;
  type: "success" | "error" | "warning";
  onClose: () => void;
}) {
  useEffect(() => {
    const t = setTimeout(onClose, 4000);
    return () => clearTimeout(t);
  }, [onClose]);

  const colors = {
    success: "bg-emerald-50 border-emerald-300 text-emerald-800",
    error: "bg-red-50 border-red-300 text-red-800",
    warning: "bg-amber-50 border-amber-300 text-amber-800",
  };

  return (
    <div
      className={`fixed bottom-4 right-4 z-50 rounded-lg border px-4 py-3 shadow-lg transition-all ${colors[type]}`}
    >
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium">{message}</span>
        <button onClick={onClose} className="ml-2 opacity-60 hover:opacity-100">
          &times;
        </button>
      </div>
    </div>
  );
}

export default function Home() {
  // --- Config state ---
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [docType, setDocType] = useState<string>("");
  const [docTypeSlug, setDocTypeSlug] = useState<string>("");
  const [modelLabel, setModelLabel] = useState<string>("");
  const [modelId, setModelId] = useState<string>("");

  // --- Fields state ---
  const [fields, setFields] = useState<Field[]>([]);
  const [instructions, setInstructions] = useState<string>("");
  const [showInstructions, setShowInstructions] = useState(false);

  // --- PDF state ---
  const [pdfUrl, setPdfUrl] = useState<string>("");
  const [uploadId, setUploadId] = useState<string>("");
  const [uploadInfo, setUploadInfo] = useState<UploadResponse | null>(null);
  const [targetPage, setTargetPage] = useState<number | null>(null);
  const [highlightKeyword, setHighlightKeyword] = useState<string | null>(null);

  // --- Ordre client text state ---
  const [ordreClientText, setOrdreClientText] = useState<string>("");

  // --- Results state ---
  const [results, setResults] = useState<ExtractionResult[]>([]);
  const [extracting, setExtracting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savingInstructions, setSavingInstructions] = useState(false);
  const [uploading, setUploading] = useState(false);

  // --- Toast ---
  const [toast, setToast] = useState<{
    message: string;
    type: "success" | "error" | "warning";
  } | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const notify = useCallback(
    (message: string, type: "success" | "error" | "warning" = "success") => {
      setToast({ message, type });
    },
    []
  );

  // --- Load config on mount ---
  useEffect(() => {
    fetchConfig().then((cfg) => {
      setConfig(cfg);
      const docTypes = Object.entries(cfg.doc_types);
      if (docTypes.length > 0) {
        setDocType(docTypes[0][0]);
        setDocTypeSlug(docTypes[0][1]);
      }
      const models = Object.entries(cfg.models);
      if (models.length > 0) {
        setModelLabel(models[0][0]);
        setModelId(models[0][1]);
      }
    });
  }, []);

  // --- Load fields when doc type changes ---
  useEffect(() => {
    if (!docTypeSlug) return;
    fetchFields(docTypeSlug).then((data) => {
      setFields(data.fields);
      setInstructions(data.instructions);
      setResults([]);
      setOrdreClientText("");
    });
  }, [docTypeSlug]);

  // --- Handle PDF upload ---
  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      notify("Seuls les fichiers PDF sont acceptés", "warning");
      return;
    }

    setUploading(true);
    setResults([]);
    try {
      const res = await uploadPdf(file);
      setUploadId(res.upload_id);
      setUploadInfo(res);
      setPdfUrl(URL.createObjectURL(file));
      const ocrTag = res.used_ocr ? " (OCR)" : "";
      notify(`${res.page_count} page(s) chargée(s)${ocrTag}`, "success");
    } catch (err: unknown) {
      notify(
        err instanceof Error ? err.message : "Erreur lors de l'upload",
        "error"
      );
    } finally {
      setUploading(false);
    }
  };

  // --- Handle extraction ---
  const isOrdreClient = docTypeSlug === "ordre-client";

  const handleExtract = async () => {
    if (isOrdreClient) {
      if (!ordreClientText.trim()) {
        notify("Collez d'abord le texte de l'ordre client", "warning");
        return;
      }
    } else {
      if (!uploadId) {
        notify("Importez d'abord un document PDF", "warning");
        return;
      }
    }
    if (fields.length === 0) {
      notify("Aucun champ défini", "warning");
      return;
    }

    setExtracting(true);
    try {
      const res = await runExtraction({
        ...(isOrdreClient
          ? { raw_text: ordreClientText }
          : { upload_id: uploadId }),
        doc_type: docTypeSlug,
        model: modelId,
        fields,
        instructions,
      });
      setResults(res.results);
      if (!isOrdreClient) {
        // Switch to backend-served PDF (may have OCR text layer for highlights)
        setPdfUrl(`${API_BASE}/api/pdf/${uploadId}`);
      }
      notify(`${res.results.length} champ(s) extraits`, "success");
    } catch (err: unknown) {
      notify(
        err instanceof Error ? err.message : "Erreur lors de l'extraction",
        "error"
      );
    } finally {
      setExtracting(false);
    }
  };

  // --- Handle save ---
  const handleSave = async () => {
    if (!config?.has_promptvault) {
      notify("PromptVault non configuré", "warning");
      return;
    }
    setSaving(true);
    try {
      await saveFields({ doc_type: docTypeSlug, fields, instructions });
      notify("Champs sauvegardés", "success");
    } catch (err: unknown) {
      notify(
        err instanceof Error ? err.message : "Erreur lors de la sauvegarde",
        "error"
      );
    } finally {
      setSaving(false);
    }
  };

  const handleSaveInstructions = async () => {
    if (!config?.has_promptvault) {
      notify("PromptVault non configuré", "warning");
      return;
    }
    setSavingInstructions(true);
    try {
      await saveInstructions({ instructions });
      notify("Instructions sauvegardées", "success");
    } catch (err: unknown) {
      notify(
        err instanceof Error ? err.message : "Erreur lors de la sauvegarde",
        "error"
      );
    } finally {
      setSavingInstructions(false);
    }
  };

  // --- Field editing ---
  const updateField = (index: number, key: keyof Field, value: string) => {
    setFields((prev) => {
      const updated = [...prev];
      updated[index] = { ...updated[index], [key]: value };
      return updated;
    });
  };

  const addField = () => {
    setFields((prev) => [
      ...prev,
      { id: crypto.randomUUID().slice(0, 8), label: "", description: "" },
    ]);
  };

  const removeField = (index: number) => {
    setFields((prev) => prev.filter((_, i) => i !== index));
  };

  // --- Navigate to page in PDF and highlight value ---
  const goToPage = (page: number, keyword?: string) => {
    // Clear first so re-clicking the same ref triggers again
    setTargetPage(null);
    setHighlightKeyword(null);
    setTimeout(() => {
      setTargetPage(page);
      if (keyword && keyword !== "Non trouvé") {
        // Use first 80 chars max to keep search targeted
        setHighlightKeyword(keyword.length > 80 ? keyword.slice(0, 80) : keyword);
      }
    }, 50);
    // Reset after navigation + highlight completes
    setTimeout(() => {
      setTargetPage(null);
    }, 1500);
  };

  // --- Build results map ---
  const resultsMap: Record<string, ExtractionResult> = {};
  for (const r of results) {
    resultsMap[r.field_id] = r;
  }

  if (!config) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="flex items-center gap-3 text-gray-500">
          <svg
            className="h-5 w-5 animate-spin"
            viewBox="0 0 24 24"
            fill="none"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
          Chargement...
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col bg-gray-50">
      {/* Header */}
      <header className="flex items-center justify-between border-b bg-white px-6 py-4 shadow-sm">
        <div className="flex items-center gap-4">
          <div>
            <h1 className="text-lg font-bold tracking-tight text-gray-900">
              Extracteur de Documents
            </h1>
            <div className="mt-1 h-0.5 w-12 rounded bg-red-500" />
          </div>
          <span className="text-xs font-medium tracking-wide text-gray-400 uppercase">
            Lextenso &mdash; Annonces &amp; Formalit&eacute;s
          </span>
        </div>
        <div className="flex items-center gap-3">
          <select
            value={docType}
            onChange={(e) => {
              const label = e.target.value;
              setDocType(label);
              setDocTypeSlug(config.doc_types[label]);
            }}
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 shadow-sm focus:border-red-500 focus:outline-none focus:ring-1 focus:ring-red-500"
          >
            {Object.keys(config.doc_types).map((label) => (
              <option key={label} value={label}>
                {label}
              </option>
            ))}
          </select>
          <select
            value={modelLabel}
            onChange={(e) => {
              setModelLabel(e.target.value);
              setModelId(config.models[e.target.value]);
            }}
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 shadow-sm focus:border-red-500 focus:outline-none focus:ring-1 focus:ring-red-500"
          >
            {Object.keys(config.models).map((label) => (
              <option key={label} value={label}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </header>

      {/* Main content */}
      <div className="flex min-h-0 flex-1">
        {/* Left panel - Fields & Results */}
        <div className="flex w-1/2 flex-col border-r bg-white">
          <div className="flex-1 overflow-y-auto p-5">
            {/* Instructions toggle */}
            <button
              onClick={() => setShowInstructions(!showInstructions)}
              className="mb-3 flex w-full items-center gap-2 text-left text-sm font-medium text-gray-600 hover:text-gray-800"
            >
              <svg
                className={`h-4 w-4 transition-transform ${showInstructions ? "rotate-90" : ""}`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M9 5l7 7-7 7"
                />
              </svg>
              Instructions d&apos;extraction
            </button>
            {showInstructions && (
              <div className="mb-4">
                <textarea
                  value={instructions}
                  onChange={(e) => setInstructions(e.target.value)}
                  rows={8}
                  className="w-full rounded-md border border-gray-300 p-3 text-sm text-gray-700 focus:border-red-500 focus:outline-none focus:ring-1 focus:ring-red-500"
                />
                <div className="mt-1.5 flex items-center justify-between">
                  <span className="text-xs text-gray-400">
                    Ce prompt est envoy&eacute; au LLM pour guider l&apos;extraction
                  </span>
                  {config.has_promptvault && (
                    <button
                      onClick={handleSaveInstructions}
                      disabled={savingInstructions}
                      className="text-xs font-medium text-red-600 hover:text-red-700 disabled:opacity-50"
                    >
                      {savingInstructions ? "Sauvegarde..." : "Sauvegarder le prompt"}
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* Fields table */}
            <div className="mb-4">
              <div className="mb-2 flex items-center justify-between">
                <h2 className="text-sm font-semibold text-gray-700">
                  Champs à extraire
                </h2>
                <button
                  onClick={addField}
                  className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-50"
                >
                  <svg
                    className="h-4 w-4"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M12 4v16m8-8H4"
                    />
                  </svg>
                  Ajouter
                </button>
              </div>

              <div className="overflow-hidden rounded-lg border border-gray-200">
                <table className="w-full">
                  <thead>
                    <tr className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      <th className="px-3 py-2">Libellé</th>
                      <th className="px-3 py-2">Description</th>
                      {results.length > 0 && (
                        <th className="px-3 py-2">Valeur extraite</th>
                      )}
                      <th className="w-8 px-2 py-2" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {fields.map((field, i) => {
                      const result = resultsMap[field.id];
                      const page = result?.pdf_reference
                        ? parsePageNumber(result.pdf_reference)
                        : null;

                      return (
                        <tr key={field.id} className="group hover:bg-gray-50/50">
                          <td className="px-3 py-1.5 align-top">
                            <input
                              type="text"
                              value={field.label}
                              onChange={(e) =>
                                updateField(i, "label", e.target.value)
                              }
                              placeholder="Libellé"
                              className="w-full rounded border-transparent bg-transparent px-1 py-0.5 text-sm text-gray-800 focus:border-red-300 focus:bg-white focus:outline-none focus:ring-1 focus:ring-red-300"
                            />
                          </td>
                          <td className="px-3 py-1.5 align-top">
                            <textarea
                              ref={(el) => {
                                if (el) {
                                  el.style.height = "auto";
                                  el.style.height = el.scrollHeight + "px";
                                }
                              }}
                              value={field.description}
                              onChange={(e) =>
                                updateField(i, "description", e.target.value)
                              }
                              placeholder="Description du champ"
                              rows={1}
                              className="w-full resize-none overflow-hidden rounded border-transparent bg-transparent px-1 py-0.5 text-sm text-gray-600 focus:border-red-300 focus:bg-white focus:outline-none focus:ring-1 focus:ring-red-300"
                            />
                          </td>
                          {results.length > 0 && (
                            <td className="px-3 py-1.5">
                              <div className="flex items-center gap-1.5">
                                {result ? (
                                  <>
                                    <span
                                      className={`text-sm ${
                                        result.value &&
                                        result.value !== "Non trouvé"
                                          ? "text-gray-800"
                                          : "italic text-gray-400"
                                      }`}
                                    >
                                      {result.value || "Non trouvé"}
                                    </span>
                                    {page && (
                                      <button
                                        onClick={() => goToPage(page, result.value)}
                                        className="ml-1 inline-flex shrink-0 items-center gap-0.5 rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-xs font-medium text-red-700 transition-colors hover:bg-red-100"
                                        title={
                                          result.pdf_reference ||
                                          `Page ${page}`
                                        }
                                      >
                                        <svg
                                          className="h-3 w-3"
                                          fill="none"
                                          viewBox="0 0 24 24"
                                          stroke="currentColor"
                                        >
                                          <path
                                            strokeLinecap="round"
                                            strokeLinejoin="round"
                                            strokeWidth={2}
                                            d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                                          />
                                        </svg>
                                        p.{page}
                                      </button>
                                    )}
                                  </>
                                ) : (
                                  <span className="text-sm italic text-gray-400">
                                    —
                                  </span>
                                )}
                              </div>
                            </td>
                          )}
                          <td className="px-2 py-1.5">
                            <button
                              onClick={() => removeField(i)}
                              className="rounded p-0.5 text-gray-300 opacity-0 transition-opacity hover:text-red-500 group-hover:opacity-100"
                            >
                              <svg
                                className="h-4 w-4"
                                fill="none"
                                viewBox="0 0 24 24"
                                stroke="currentColor"
                              >
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  strokeWidth={2}
                                  d="M6 18L18 6M6 6l12 12"
                                />
                              </svg>
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                    {fields.length === 0 && (
                      <tr>
                        <td
                          colSpan={results.length > 0 ? 4 : 3}
                          className="px-3 py-6 text-center text-sm text-gray-400"
                        >
                          Aucun champ. Cliquez &quot;Ajouter&quot; pour
                          commencer.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* Action buttons - sticky bottom */}
          <div className="border-t bg-white px-5 py-3">
            <div className="flex gap-3">
              <button
                onClick={handleExtract}
                disabled={extracting || (!isOrdreClient && !uploadId) || (isOrdreClient && !ordreClientText.trim()) || fields.length === 0}
                className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-red-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {extracting ? (
                  <>
                    <svg
                      className="h-4 w-4 animate-spin"
                      viewBox="0 0 24 24"
                      fill="none"
                    >
                      <circle
                        className="opacity-25"
                        cx="12"
                        cy="12"
                        r="10"
                        stroke="currentColor"
                        strokeWidth="4"
                      />
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                      />
                    </svg>
                    Extraction en cours...
                  </>
                ) : (
                  <>
                    <svg
                      className="h-4 w-4"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M13 10V3L4 14h7v7l9-11h-7z"
                      />
                    </svg>
                    Extraire
                  </>
                )}
              </button>
              {config.has_promptvault && (
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="flex items-center gap-2 rounded-lg border border-red-300 px-4 py-2.5 text-sm font-medium text-red-700 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {saving ? (
                    <svg
                      className="h-4 w-4 animate-spin"
                      viewBox="0 0 24 24"
                      fill="none"
                    >
                      <circle
                        className="opacity-25"
                        cx="12"
                        cy="12"
                        r="10"
                        stroke="currentColor"
                        strokeWidth="4"
                      />
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                      />
                    </svg>
                  ) : (
                    <svg
                      className="h-4 w-4"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4"
                      />
                    </svg>
                  )}
                  Sauvegarder
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Right panel - PDF Viewer or Ordre client text */}
        <div className="flex w-1/2 flex-col bg-gray-100">
          {isOrdreClient ? (
            <>
              <div className="flex items-center gap-3 border-b bg-white px-5 py-3">
                <span className="text-sm font-medium text-gray-700">
                  Texte de l&apos;ordre client
                </span>
              </div>
              <div className="flex-1 overflow-hidden p-4">
                <textarea
                  value={ordreClientText}
                  onChange={(e) => setOrdreClientText(e.target.value)}
                  placeholder="Collez ou saisissez le texte de l'ordre client ici..."
                  className="h-full w-full resize-none rounded-lg border border-gray-300 bg-white p-4 text-sm text-gray-700 focus:border-red-500 focus:outline-none focus:ring-1 focus:ring-red-500"
                />
              </div>
            </>
          ) : (
            <>
              <div className="flex items-center gap-3 border-b bg-white px-5 py-3">
                <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-dashed border-gray-300 px-4 py-2 text-sm font-medium text-gray-600 transition-colors hover:border-red-400 hover:bg-red-50 hover:text-red-600">
                  <svg
                    className="h-4 w-4"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
                    />
                  </svg>
                  {uploading ? "Chargement..." : "Importer un PDF"}
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf"
                    onChange={handleFileUpload}
                    className="hidden"
                  />
                </label>
                {uploadInfo && (
                  <span className="text-sm text-gray-500">
                    {uploadInfo.filename} &mdash; {uploadInfo.page_count} page(s)
                    {uploadInfo.used_ocr && (
                      <span className="ml-1 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700">
                        OCR
                      </span>
                    )}
                  </span>
                )}
              </div>

              <div className="flex-1 overflow-hidden">
                {pdfUrl ? (
                  <PdfViewer fileUrl={pdfUrl} targetPage={targetPage} highlightKeyword={highlightKeyword} />
                ) : (
                  <div className="flex h-full flex-col items-center justify-center gap-3 text-gray-400">
                    <svg
                      className="h-16 w-16"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={1}
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                      />
                    </svg>
                    <p className="text-sm">Importez un PDF pour commencer</p>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Toast notifications */}
      {toast && (
        <Toast
          message={toast.message}
          type={toast.type}
          onClose={() => setToast(null)}
        />
      )}
    </div>
  );
}
