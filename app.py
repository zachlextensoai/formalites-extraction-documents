import json
import re
import tempfile
import os
import uuid
from pathlib import Path

from nicegui import ui, app
from nicegui_pdf import PdfViewer

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


# --- Init PromptVault client ---
pv_client = PromptVaultClient(PROMPTVAULT_BASE_URL, PROMPTVAULT_API_KEY) if PROMPTVAULT_API_KEY else None


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


def parse_page_number(ref: str) -> int | None:
    m = re.search(r"[Pp]age\s*(\d+)", ref)
    return int(m.group(1)) if m else None


# --- Per-client state ---
class AppState:
    def __init__(self):
        self.doc_type_slug: str = list(DOC_TYPES.values())[0]
        self.doc_type_label: str = list(DOC_TYPES.keys())[0]
        self.model_id: str = list(AVAILABLE_MODELS.values())[0]
        self.fields: list[FieldDefinition] = load_fields(self.doc_type_slug)
        self.results: list[ExtractionResult] = []
        self.instructions: str = load_instructions(self.doc_type_slug)
        self.pdf_bytes: bytes | None = None
        self.pdf_text: str = ""
        self.pdf_path: str | None = None
        self.pdf_page_count: int = 0
        self.used_ocr: bool = False


# --- Main page ---
@ui.page("/")
def main_page():
    state = AppState()

    # References to UI elements we need to update
    pdf_viewer_ref: PdfViewer | None = None
    results_container = None
    fields_grid = None
    status_label = None
    pdf_container = None
    page_label = None

    # --- Callbacks ---
    def on_doc_type_change(e):
        state.doc_type_slug = DOC_TYPES[e.value]
        state.doc_type_label = e.value
        state.fields = load_fields(state.doc_type_slug)
        state.instructions = load_instructions(state.doc_type_slug)
        state.results = []
        instructions_area.value = state.instructions
        refresh_grid()
        refresh_results()

    def on_model_change(e):
        state.model_id = AVAILABLE_MODELS[e.value]

    async def on_file_upload(e):
        nonlocal pdf_viewer_ref
        state.pdf_bytes = e.content.read()
        state.pdf_text = ""
        state.results = []
        refresh_results()

        # Save to temp file for PdfViewer
        if state.pdf_path and os.path.exists(state.pdf_path):
            os.unlink(state.pdf_path)

        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.write(fd, state.pdf_bytes)
        os.close(fd)
        state.pdf_path = path

        # Expose the temp file as a static resource
        resource_url = app.add_media_file(local_file=path, url_path=f"/pdf/{uuid.uuid4().hex}.pdf")

        # Extract text
        status_label.text = "Extraction du texte..."
        result = extract_text_from_pdf(state.pdf_bytes)
        state.pdf_text = result["text"]
        state.pdf_page_count = result["page_count"]
        state.used_ocr = result["used_ocr"]

        ocr_tag = " (OCR)" if state.used_ocr else ""
        status_label.text = f"{state.pdf_page_count} page(s) chargée(s){ocr_tag}"

        # Render PDF viewer
        pdf_container.clear()
        with pdf_container:
            with ui.row().classes("w-full items-center justify-center gap-2 mb-2"):
                ui.button(icon="navigate_before", on_click=lambda: go_to_page(-1, relative=True)).props("flat dense")
                page_lbl = ui.label("Page 1").classes("text-sm")
                ui.button(icon="navigate_next", on_click=lambda: go_to_page(1, relative=True)).props("flat dense")

            viewer = PdfViewer(resource_url)
            viewer.classes("w-full").style("height: 750px")
            pdf_viewer_ref = viewer

            # Update page label when page changes
            def update_page_label():
                page_lbl.text = f"Page {viewer.current_page} / {state.pdf_page_count}"
            viewer.on("change_current_page", lambda _: update_page_label())

    def go_to_page(page: int, relative: bool = False):
        nonlocal pdf_viewer_ref
        if not pdf_viewer_ref:
            return
        if relative:
            target = pdf_viewer_ref.current_page + page
        else:
            target = page
        target = max(1, min(state.pdf_page_count, target))
        pdf_viewer_ref.current_page = target

    def refresh_grid():
        nonlocal fields_grid
        if fields_grid:
            rows = [{"id": f.id, "label": f.label, "description": f.description} for f in state.fields]
            fields_grid.options["rowData"] = rows
            fields_grid.update()

    def sync_fields_from_grid():
        """Read current grid data back into state.fields."""
        # AG Grid edits update rowData in-place via the grid options
        rows = fields_grid.options["rowData"]
        state.fields = []
        for row in rows:
            label = (row.get("label") or "").strip()
            desc = (row.get("description") or "").strip()
            if not label and not desc:
                continue
            fid = row.get("id") or uuid.uuid4().hex[:8]
            state.fields.append(FieldDefinition(
                id=str(fid),
                label=label or "Nouveau champ",
                description=desc or "",
            ))

    def add_field():
        new_id = uuid.uuid4().hex[:8]
        state.fields.append(FieldDefinition(id=new_id, label="Nouveau champ", description=""))
        refresh_grid()

    def delete_selected():
        selected = fields_grid.get_selected_rows()
        if not selected:
            ui.notify("Sélectionnez une ligne à supprimer", type="warning")
            return
        selected_ids = {r["id"] for r in selected}
        state.fields = [f for f in state.fields if f.id not in selected_ids]
        refresh_grid()

    async def run_extraction():
        sync_fields_from_grid()
        state.instructions = instructions_area.value

        if not state.pdf_bytes:
            ui.notify("Importez d'abord un document PDF", type="warning")
            return
        if not OPENROUTER_API_KEY:
            ui.notify("Clé API OpenRouter manquante", type="negative")
            return
        if not state.fields:
            ui.notify("Aucun champ défini", type="warning")
            return

        spinner = ui.spinner("dots", size="lg")
        try:
            # Extract text fresh from bytes
            pdf_text = state.pdf_text
            if not pdf_text:
                result = extract_text_from_pdf(state.pdf_bytes)
                pdf_text = result["text"]
                state.pdf_text = pdf_text

            if not pdf_text:
                ui.notify("Impossible d'extraire le texte du PDF", type="negative")
                return

            results = extract_fields(
                pdf_text,
                state.fields,
                OPENROUTER_API_KEY,
                state.model_id,
                instructions=state.instructions,
            )
            state.results = results
            refresh_results()
            ui.notify(f"{len(results)} champ(s) extraits", type="positive")
        except Exception as e:
            ui.notify(f"Erreur : {e}", type="negative")
        finally:
            spinner.delete()

    async def save_fields():
        sync_fields_from_grid()
        state.instructions = instructions_area.value

        if not pv_client:
            ui.notify("PromptVault non configuré", type="warning")
            return
        ok = pv_client.save_fields(
            state.doc_type_slug,
            PROMPTVAULT_PROJECT_SLUG,
            state.fields,
            doc_type_label=state.doc_type_label,
            instructions=state.instructions,
        )
        if ok:
            ui.notify("Champs sauvegardés", type="positive")
        else:
            ui.notify("Erreur lors de la sauvegarde", type="negative")

    def refresh_results():
        if not results_container:
            return
        results_container.clear()
        if not state.results:
            return

        results_map = {r.field_id: r for r in state.results}
        with results_container:
            ui.separator()
            ui.label("Résultats de l'extraction").classes("text-lg font-bold mt-2")
            for f in state.fields:
                r = results_map.get(f.id)
                if not r:
                    continue
                with ui.card().classes("w-full q-pa-sm q-mb-sm"):
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label(f.label).classes("text-sm font-bold text-primary")
                        if r.pdf_reference:
                            page = parse_page_number(r.pdf_reference)
                            if page:
                                ui.button(
                                    f"Page {page}",
                                    on_click=lambda p=page: go_to_page(p),
                                    icon="description",
                                ).props("flat dense color=accent size=sm")
                            else:
                                ui.label(r.pdf_reference).classes("text-xs text-grey")
                    value = r.value if r.value and r.value != "Non trouvé" else "Non trouvé"
                    style = "text-grey-6 italic" if value == "Non trouvé" else ""
                    ui.label(value).classes(f"text-sm {style}")

    # --- UI Layout ---
    ui.colors(primary="#1976d2", accent="#ff9800")

    with ui.header().classes("items-center justify-between q-px-lg"):
        ui.label("Extracteur de Documents").classes("text-h6 text-white")

    with ui.splitter(value=50).classes("w-full h-full") as splitter:

        # --- Left panel ---
        with splitter.before:
            with ui.column().classes("w-full q-pa-md gap-2"):
                # Doc type + model selectors
                with ui.row().classes("w-full gap-4"):
                    ui.select(
                        list(DOC_TYPES.keys()),
                        value=state.doc_type_label,
                        label="Type de document",
                        on_change=on_doc_type_change,
                    ).classes("w-1/2")
                    ui.select(
                        list(AVAILABLE_MODELS.keys()),
                        value=list(AVAILABLE_MODELS.keys())[0],
                        label="Modèle LLM",
                        on_change=on_model_change,
                    ).classes("w-1/2")

                # Instructions
                with ui.expansion("Instructions d'extraction", icon="edit_note").classes("w-full"):
                    instructions_area = ui.textarea(
                        value=state.instructions,
                    ).classes("w-full").props("outlined rows=8")

                # Editable fields table
                ui.label("Champs à extraire").classes("text-lg font-bold")
                row_data = [{"id": f.id, "label": f.label, "description": f.description} for f in state.fields]
                fields_grid = ui.aggrid({
                    "columnDefs": [
                        {"field": "id", "hide": True},
                        {
                            "field": "label",
                            "headerName": "Libellé",
                            "editable": True,
                            "flex": 1,
                        },
                        {
                            "field": "description",
                            "headerName": "Description",
                            "editable": True,
                            "flex": 2,
                        },
                    ],
                    "rowData": row_data,
                    "rowSelection": "single",
                    "stopEditingWhenCellsLoseFocus": True,
                    "domLayout": "autoHeight",
                }).classes("w-full")

                # Grid action buttons
                with ui.row().classes("w-full gap-2"):
                    ui.button("Ajouter un champ", icon="add", on_click=add_field).props("flat dense")
                    ui.button("Supprimer", icon="delete", on_click=delete_selected).props("flat dense color=negative")

                # Main action buttons
                with ui.row().classes("w-full gap-4 mt-2"):
                    ui.button(
                        "Extraire",
                        icon="auto_awesome",
                        on_click=run_extraction,
                        color="primary",
                    ).classes("flex-1")
                    ui.button(
                        "Sauvegarder les champs",
                        icon="save",
                        on_click=save_fields,
                    ).classes("flex-1").props("outline" if pv_client else "outline disabled")

                # Results section
                results_container = ui.column().classes("w-full")

        # --- Right panel ---
        with splitter.after:
            with ui.column().classes("w-full q-pa-md gap-2"):
                ui.label("Document PDF").classes("text-lg font-bold")

                status_label = ui.label("").classes("text-sm text-grey")

                ui.upload(
                    label="Importer un PDF",
                    auto_upload=True,
                    on_upload=on_file_upload,
                ).props('accept=".pdf" flat bordered').classes("w-full")

                pdf_container = ui.column().classes("w-full")


ui.run(
    title="Extracteur de Documents",
    port=8080,
    reload=True,
)
