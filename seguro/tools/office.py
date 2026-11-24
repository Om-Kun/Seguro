import os
import win32com.client
import openpyxl
from openpyxl.chart import BarChart, Reference
from docx import Document
from seguro.tools.registry import registry
from seguro.safety.gate import safety_gate
from seguro.tools.browser import browser_open

# ---------------------------------------------------------------------------
# GOOGLE WORKSPACE TOOLS (Google Sheets, Google Docs, Google Slides)
# Pinned to Brave Browser for users without local MS Office
# ---------------------------------------------------------------------------

@registry.register(
    name="google_sheets_open",
    description="Creates or opens a new Google Sheets spreadsheet in Brave Browser (sheets.new).",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Optional title or purpose of the sheet"}
        }
    }
)
def google_sheets_open(title: str = "") -> dict:
    url = "https://sheets.new"
    res = browser_open(url)
    return {"ok": True, "app": "google_sheets", "url": url, "browser": res.get("browser", "brave"), "title": title}

@registry.register(
    name="google_docs_open",
    description="Creates or opens a new Google Docs document in Brave Browser (docs.new).",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Optional title or topic of the document"}
        }
    }
)
def google_docs_open(title: str = "") -> dict:
    url = "https://docs.new"
    res = browser_open(url)
    return {"ok": True, "app": "google_docs", "url": url, "browser": res.get("browser", "brave"), "title": title}

@registry.register(
    name="google_slides_open",
    description="Creates or opens a new Google Slides presentation in Brave Browser (slides.new).",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Optional title or topic of the presentation"}
        }
    }
)
def google_slides_open(title: str = "") -> dict:
    url = "https://slides.new"
    res = browser_open(url)
    return {"ok": True, "app": "google_slides", "url": url, "browser": res.get("browser", "brave"), "title": title}

# ---------------------------------------------------------------------------
# LOCAL FILE GENERATORS (Independent of Microsoft Office installation)
# Uses openpyxl and python-docx to generate physical files on disk
# ---------------------------------------------------------------------------

def _parse_list_arg(val) -> list:
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        val = val.strip()
        try:
            import json
            return json.loads(val)
        except Exception:
            try:
                import ast
                return ast.literal_eval(val)
            except Exception:
                return [v.strip() for v in val.split(",") if v.strip()]
    return list(val) if val else []

def _out_path(filename: str, default_ext: str) -> str:
    """Bare names go to the Desktop; ~/... and absolute paths are honoured."""
    from seguro.tools.files import normalize_user_path
    name = (filename or "").strip() or f"output{default_ext}"
    if not name.lower().endswith(default_ext):
        name += default_ext
    return normalize_user_path(name)


SOFFICE_PATH = r"C:\Program Files\LibreOffice\program\soffice.exe"
SCALC_PATH = r"C:\Program Files\LibreOffice\program\scalc.exe"
SWRITER_PATH = r"C:\Program Files\LibreOffice\program\swriter.exe"
SIMPRESS_PATH = r"C:\Program Files\LibreOffice\program\simpress.exe"


def _launch_office_app(exe: str, path: str = "") -> dict:
    import subprocess
    from seguro.tools.files import normalize_user_path
    if not os.path.exists(exe):
        exe = SOFFICE_PATH if os.path.exists(SOFFICE_PATH) else exe
    if not os.path.exists(exe):
        return {"ok": False, "error": f"LibreOffice executable not found at '{exe}'."}
    cmd = [exe]
    if path:
        norm = normalize_user_path(path)
        if os.path.exists(norm):
            cmd.append(norm)
    subprocess.Popen(cmd)
    return {"ok": True, "app": os.path.basename(exe), "file": path or "blank_document"}


@registry.register(
    name="libreoffice_calc_open",
    description="Opens LibreOffice Calc (spreadsheet). If filename is provided, opens that spreadsheet; otherwise opens a blank sheet.",
    parameters={"type": "object", "properties": {"filename": {"type": "string", "description": "Optional spreadsheet file to open"}}}
)
def libreoffice_calc_open(filename: str = "") -> dict:
    return _launch_office_app(SCALC_PATH, filename)


@registry.register(
    name="libreoffice_writer_open",
    description="Opens LibreOffice Writer (word processor). If filename is provided, opens that document; otherwise opens a blank document.",
    parameters={"type": "object", "properties": {"filename": {"type": "string", "description": "Optional document file to open"}}}
)
def libreoffice_writer_open(filename: str = "") -> dict:
    return _launch_office_app(SWRITER_PATH, filename)


@registry.register(
    name="libreoffice_impress_open",
    description="Opens LibreOffice Impress (presentation). If filename is provided, opens that presentation; otherwise opens a blank presentation.",
    parameters={"type": "object", "properties": {"filename": {"type": "string", "description": "Optional presentation file to open"}}}
)
def libreoffice_impress_open(filename: str = "") -> dict:
    return _launch_office_app(SIMPRESS_PATH, filename)


@registry.register(
    name="libreoffice_open",
    description="Opens any document, spreadsheet, or presentation in LibreOffice.",
    parameters={"type": "object", "properties": {"filename": {"type": "string", "description": "Optional file path to open in LibreOffice"}}}
)
def libreoffice_open(filename: str = "") -> dict:
    return _launch_office_app(SOFFICE_PATH, filename)


def _maybe_open(path: str, open_after: bool):
    # Opening the file in a viewer locks it (LibreOffice/Excel), which breaks later edits and
    # retries, so it only happens when explicitly asked.
    if open_after:
        soffice = r"C:\Program Files\LibreOffice\program\soffice.exe"
        if os.path.exists(soffice):
            try:
                import subprocess
                subprocess.Popen([soffice, path])
                return
            except Exception:
                pass
        try:
            os.startfile(path)
        except Exception:
            pass


@registry.register(
    name="excel_create_table_and_chart",
    description="Creates an Excel spreadsheet (.xlsx) with a table and an embedded bar chart. Bare filenames go to the Desktop.",
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "Output file name or path (e.g. 'states.xlsx')"},
            "headers": {"type": "array", "items": {"type": "string"}, "description": "Column header names"},
            "rows": {"type": "array", "items": {"type": "array"}, "description": "Rows of data values (numbers as numbers)"},
            "chart_title": {"type": "string", "description": "Title of the embedded chart"},
            "open_after": {"type": "boolean", "description": "Open the file for the user afterwards (default false)"}
        },
        "required": ["headers", "rows"]
    }
)
def excel_create_table_and_chart(headers: list, rows: list, filename: str = "report.xlsx",
                                 chart_title: str = "Analysis Chart", open_after: bool = False) -> dict:
    headers = [str(h) for h in _parse_list_arg(headers)]
    rows = [(_parse_list_arg(r) if isinstance(r, str) else list(r)) for r in _parse_list_arg(rows)]
    out_path = _out_path(filename, ".xlsx")

    ok, reason = safety_gate.check_path(out_path)
    if not ok:
        return {"ok": False, "error": reason}

    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "ReportData"
        ws.append(headers)
        for r in rows:
            ws.append(r)

        if len(headers) >= 2 and len(rows) > 0:
            chart = BarChart()
            chart.title = chart_title
            chart.style = 10
            data_ref = Reference(ws, min_col=2, min_row=1, max_row=len(rows)+1)
            cats_ref = Reference(ws, min_col=1, min_row=2, max_row=len(rows)+1)
            chart.add_data(data_ref, titles_from_data=True)
            chart.set_categories(cats_ref)
            ws.add_chart(chart, "D2")

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        wb.save(out_path)
        _maybe_open(out_path, open_after)
        return {"ok": True, "method": "openpyxl", "path": out_path, "rows": len(rows),
                "table": [headers] + rows}
    except PermissionError:
        return {"ok": False, "error": f"'{out_path}' is locked, probably open in another program. "
                                      f"Use a different filename or ask the user to close it."}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

@registry.register(
    name="word_create_document",
    description="Creates a Word (.docx) document with a title, paragraphs and optional images (e.g. a chart PNG). Bare filenames go to the Desktop.",
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "Output file name or path (e.g. 'summary.docx')"},
            "title": {"type": "string", "description": "Document title"},
            "paragraphs": {"type": "array", "items": {"type": "string"}, "description": "Paragraph texts"},
            "images": {"type": "array", "items": {"type": "string"}, "description": "Optional image file paths to embed"},
            "open_after": {"type": "boolean", "description": "Open the file for the user afterwards (default false)"}
        },
        "required": ["title", "paragraphs"]
    }
)
def word_create_document(title: str, paragraphs: list, filename: str = "document.docx", images: list = None,
                         open_after: bool = False) -> dict:
    from docx.shared import Inches
    from seguro.tools.files import normalize_user_path
    paragraphs = [str(p) for p in _parse_list_arg(paragraphs)]
    image_paths = [normalize_user_path(str(i)) for i in _parse_list_arg(images or [])]
    missing = [i for i in image_paths if not os.path.isfile(i)]
    if missing:
        return {"ok": False, "error": f"Image file(s) not found: {missing}"}
    out_path = _out_path(filename, ".docx")

    ok, reason = safety_gate.check_path(out_path)
    if not ok:
        return {"ok": False, "error": reason}

    try:
        doc = Document()
        doc.add_heading(title, 0)
        for p in paragraphs:
            doc.add_paragraph(p)
        for img in image_paths:
            doc.add_picture(img, width=Inches(6))
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        doc.save(out_path)
        _maybe_open(out_path, open_after)
        return {"ok": True, "path": out_path, "paragraphs_count": len(paragraphs), "images": len(image_paths)}
    except PermissionError:
        return {"ok": False, "error": f"'{out_path}' is locked, probably open in another program."}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
