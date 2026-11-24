"""
Turns a request into a validated TaskGraph. The LLM output is schema-constrained (structured
outputs), then validated with pydantic + graph rules. One repair round, then a single-node fallback.
"""
import json
import os
import re
from typing import Optional, Tuple

from pydantic import ValidationError

from seguro.core.workflow.models import TaskGraph, CHECK_TYPES
from seguro.llm.client import LLMClient

ARTIFACT_WORDS = re.compile(
    r"\b(excel|xlsx|spreadsheet|sheet|word|docx|document|report|powerpoint|pptx|slide|deck|email|e-mail|gmail|"
    r"draft|chart|graph|screenshot|browser|brave|web|news|articles?|folder|downloads|flask|app|server|summary|table|csv)\b",
    re.IGNORECASE)
STEP_SPLIT = re.compile(r",|;|\bthen\b|\band\b|\bafter that\b", re.IGNORECASE)


def is_complex(request: str) -> bool:
    """Short tasks (one app, ≤3 steps) skip the planner and use the single-task loop."""
    artifacts = {m.lower() for m in ARTIFACT_WORDS.findall(request)}
    steps = [s for s in STEP_SPLIT.split(request) if len(s.strip().split()) >= 2]
    return len(steps) > 3 or (len(artifacts) >= 3 and len(steps) >= 2)


CHECK_HELP = """Check objects (success_criteria items). Use {{node_id.output_key}} to refer to outputs:
- {"type":"file_exists","path":...}            - {"type":"file_contains","path":...,"text":...}
- {"type":"xlsx_min_rows","path":...,"min_rows":N}  - {"type":"xlsx_numeric_column","path":...,"column":N,"min_count":N}
- {"type":"xlsx_has_chart","path":...}         - {"type":"docx_contains","path":...,"text":...}
- {"type":"docx_has_image","path":...}         - {"type":"pptx_slide_count","path":...,"min":N}
- {"type":"pptx_has_image","path":...}         - {"type":"folder_has_files","path":...,"pattern":"*.pdf","min_count":N}
- {"type":"window_title_contains","text":...} - {"type":"http_ok","url":...}
- {"type":"gmail_draft_exists","subject_contains":...,"with_attachment":true}
- {"type":"output_present","key":...}  (added automatically for every expected_output)"""

EXAMPLE = {
    "goal": "Put 3 city populations in Excel with a chart, then a Word summary with the chart image",
    "nodes": [
        {"id": "make_sheet", "goal": "Create cities.xlsx on the Desktop with columns City, Population for 3 cities and a bar chart",
         "preferred_rung": "api", "depends_on": [], "inputs": {},
         "expected_output": {"file_path": "path", "table": "table"},
         "success_criteria": [{"type": "xlsx_min_rows", "path": "{{make_sheet.file_path}}", "min_rows": 3},
                              {"type": "xlsx_has_chart", "path": "{{make_sheet.file_path}}"}],
         "max_attempts": 2, "requires_confirmation": False, "gui": False},
        {"id": "chart_png", "goal": "Render a bar chart PNG of the city table",
         "preferred_rung": "api", "depends_on": ["make_sheet"], "inputs": {"table": "{{make_sheet.table}}"},
         "expected_output": {"image_path": "path"},
         "success_criteria": [{"type": "file_exists", "path": "{{chart_png.image_path}}"}],
         "max_attempts": 2, "requires_confirmation": False, "gui": False},
        {"id": "write_doc", "goal": "Create summary.docx on the Desktop describing the table and embedding the chart image",
         "preferred_rung": "api", "depends_on": ["make_sheet", "chart_png"],
         "inputs": {"table": "{{make_sheet.table}}", "image": "{{chart_png.image_path}}"},
         "expected_output": {"file_path": "path"},
         "success_criteria": [{"type": "docx_has_image", "path": "{{write_doc.file_path}}"}],
         "max_attempts": 2, "requires_confirmation": False, "gui": False},
    ],
}


def _system_prompt(tool_lines: str) -> str:
    return f"""You plan multi-step Windows desktop tasks for the Seguro agent as a dependency graph (JSON).
Rules:
- Each node is ONE concrete subtask with a one-sentence goal that a tool-calling agent can finish alone.
- id: short snake_case (e.g. make_sheet). depends_on lists ids of nodes whose outputs it needs.
- expected_output: what the node must publish, as key -> kind (path|text|table|url|json|number).
- inputs: name -> literal value or "{{{{other_node.output_key}}}}" referring to an upstream expected_output.
- success_criteria: checks that prove the subtask worked, verifiable in code. Prefer file/content checks.
- preferred_rung: skill|shell|api|uia|vision|delegate|any. Prefer api/shell over GUI (uia/vision).
- gui: true only if the node must use mouse/keyboard on visible windows.
- requires_confirmation: true for sending, deleting, overwriting, purchasing, posting, or changing settings.
- Do not add nodes for "return results" or "finish". 2 to 8 nodes. No cycles.
- If the request names a file or folder, put the FULL path (e.g. ~/Desktop/reports/sales.xlsx) in the node goal
  AND use that literal path in its checks (file_exists / xlsx_* / docx_* path), not a {{ref}}. Wrong location = failure.
- Only add a check the request actually asks for (no xlsx_has_chart unless a chart IN the sheet is requested).
- Email drafts: gmail_create_draft, checked with gmail_draft_exists. Never plan sending.
- News: one news_search node publishing its 'table' (a path-free table output) for later nodes.
- Charts as images: chart_png (publishes image_path); Word with images: word_create_document(images=[...]);
  slides: pptx_create; local web apps: process_start then browser_page_open/fill/click/screenshot.
{CHECK_HELP}
Tools the executing agent has:
{tool_lines}
Example output:
{json.dumps(EXAMPLE)}"""


def _tool_lines() -> str:
    from seguro.tools.registry import registry
    return "\n".join(f"- {s['function']['name']}: {s['function']['description'][:90]}"
                     for s in registry.get_openai_tools())


FILE_RE = re.compile(r"\b([\w\-]+\.(?:xlsx|docx|pptx|png|jpg|csv|json|txt|html|py|md|pdf))\b", re.IGNORECASE)
TILDE_DIR_RE = re.compile(r"(~[\\/][\w\-\\/ ]*?[\w\-]+)(?=[\s,.;:)]|$)")
NAMED_DIR_RE = re.compile(r"\bfolder\s+([\w\-]+)\s+(?:in|on)\s+(?:my|the)\s+(Desktop|Downloads|Documents)\b", re.IGNORECASE)
PLAIN_DIR_RE = re.compile(r"\b(?:on|in|to)\s+(?:my|the)\s+(Desktop|Downloads|Documents)\b", re.IGNORECASE)


def _folders_with_positions(request: str) -> list:
    found = []
    for m in TILDE_DIR_RE.finditer(request):
        p = m.group(1).replace("\\", "/").rstrip("/")
        if not FILE_RE.search(p.rsplit("/", 1)[-1]):
            found.append((m.start(), p))
    for m in NAMED_DIR_RE.finditer(request):
        found.append((m.start(), f"~/{m.group(2).capitalize()}/{m.group(1)}"))
    for m in PLAIN_DIR_RE.finditer(request):
        if not any(abs(m.start() - s) < 40 for s, _ in found):
            found.append((m.start(), f"~/{m.group(1).capitalize()}"))
    return sorted(found)


def pin_requested_paths(request: str, graph: TaskGraph) -> list:
    """For each output file the request names, make the producing node check that exact location.
    Model-chosen {{ref}} paths can point anywhere; a literal check cannot. Returns notes."""
    from seguro.core.workflow.models import FileExists
    folders = _folders_with_positions(request)
    notes = []
    for m in FILE_RE.finditer(request):
        name = m.group(1)
        before = [p for pos, p in folders if pos < m.start()]
        after = [p for pos, p in folders if pos > m.start()]
        immediate_after = [p for pos, p in folders if 0 < (pos - m.end()) < 35]
        folder = immediate_after[0] if immediate_after else ((before or after or [None])[-1 if before else 0])
        if not folder:
            continue
        full = f"{folder}/{name}"
        if os.path.exists(os.path.expanduser(full)):
            continue  # an existing input, not an output
        owners = [n for n in graph.nodes if name.lower() in n.goal.lower()]
        if not owners:
            notes.append(f"no step mentions requested file {name}; location not pinned")
            continue
        # the last step naming the file is the one expected to (re)create it in its final form
        node = owners[-1]
        if not any(isinstance(c, FileExists) and c.path.replace("\\", "/").lower() == full.lower()
                   for c in node.success_criteria):
            node.success_criteria.append(FileExists(type="file_exists", path=full))
            if folder.lower() not in node.goal.lower():
                node.goal = node.goal.replace(name, full, 1)
            notes.append(f"{node.id}: must produce {full}")
    return notes


def derive_required_checks(request: str, graph: TaskGraph) -> list:
    """Deterministically extracts from the request text required content and attaches typed checks
    to the producing node in graph:
    - named columns -> xlsx_has_columns + xlsx_min_rows(ge 1)
    - "N slides" -> pptx_slide_count
    - "sheets named X and Y" -> xlsx_has_sheets
    - "stating the sum" / literal quoted strings / SHA-256 -> docx_contains
    - "copy into folder X" / "subfolder named X" -> folder_has_files
    Returns notes describing attached checks.
    """
    from seguro.core.workflow.models import (
        FileExists, FileContains, XlsxMinRows, XlsxHasChart,
        XlsxHasColumns, XlsxHasSheets, DocxContains, PptxSlideCount, FolderHasFiles
    )
    notes = []
    folders = _folders_with_positions(request)

    def _full_path(fname):
        for m in FILE_RE.finditer(request):
            if m.group(1).lower() == fname.lower():
                before = [p for pos, p in folders if pos < m.start()]
                after = [p for pos, p in folders if pos > m.start()]
                immediate_after = [p for pos, p in folders if 0 < (pos - m.end()) < 35]
                folder = immediate_after[0] if immediate_after else ((before or after or [None])[-1 if before else 0])
                if folder:
                    return f"{folder}/{fname}"
        return fname

    # 1. Named columns for XLSX
    col_m = re.search(r"columns?\s+([a-zA-Z0-9_,\s]+?)(?:\s+and\s+([a-zA-Z0-9_]+))(?:\s+and\s+(?:a\s+)?bar\s+chart|\s+with|\s+listing|\s+in|\s+for|[,.]|$)", request, re.I)
    if col_m:
        raw_cols = col_m.group(1)
        last_col = col_m.group(2)
        cols = [c.strip() for c in re.split(r"[,;]|\s+and\s+", raw_cols) if c.strip() and c.strip().lower() not in ("a", "an", "the", "with")]
        if last_col and last_col.strip() and last_col.strip().lower() not in ("a", "bar", "chart", "with"):
            cols.append(last_col.strip())
        if cols:
            xlsx_nodes = [n for n in graph.nodes if ".xlsx" in n.goal.lower() or any(hasattr(c, "path") and ".xlsx" in str(getattr(c, "path", "")).lower() for c in n.success_criteria)]
            if xlsx_nodes:
                target_node = xlsx_nodes[-1]
                target_path = next((getattr(c, "path") for c in target_node.success_criteria if hasattr(c, "path") and ".xlsx" in str(getattr(c, "path", "")).lower()), None)
                if not target_path:
                    m_x = re.search(r"\b([\w\-]+\.xlsx)\b", request, re.I)
                    if m_x:
                        target_path = _full_path(m_x.group(1))
                if target_path:
                    if not any(isinstance(c, XlsxHasColumns) for c in target_node.success_criteria):
                        target_node.success_criteria.append(XlsxHasColumns(type="xlsx_has_columns", path=target_path, columns=cols))
                        notes.append(f"{target_node.id}: must have columns {cols}")
                    if not any(isinstance(c, XlsxMinRows) for c in target_node.success_criteria):
                        target_node.success_criteria.append(XlsxMinRows(type="xlsx_min_rows", path=target_path, min_rows=1))
                        notes.append(f"{target_node.id}: must have min_rows >= 1")

    # 2. "N slides" in PowerPoint
    pptx_m = re.search(r"(\d+)\s*(?:-|–|\s*)slides?", request, re.I)
    if pptx_m:
        n_slides = int(pptx_m.group(1))
        pptx_nodes = [n for n in graph.nodes if ".pptx" in n.goal.lower() or any(hasattr(c, "path") and ".pptx" in str(getattr(c, "path", "")).lower() for c in n.success_criteria)]
        if pptx_nodes:
            target_node = pptx_nodes[-1]
            target_path = next((getattr(c, "path") for c in target_node.success_criteria if hasattr(c, "path") and ".pptx" in str(getattr(c, "path", "")).lower()), None)
            if not target_path:
                m_p = re.search(r"\b([\w\-]+\.pptx)\b", request, re.I)
                if m_p:
                    target_path = _full_path(m_p.group(1))
            if target_path and not any(isinstance(c, PptxSlideCount) for c in target_node.success_criteria):
                target_node.success_criteria.append(PptxSlideCount(type="pptx_slide_count", path=target_path, min=n_slides, max=n_slides + 2))
                notes.append(f"{target_node.id}: must have slide count >= {n_slides}")

    # 3. Sheets named X and Y
    sheets_m = re.search(r"sheets?\s+named\s+['\"]?(\w+)['\"]?\s+and\s+['\"]?(\w+)['\"]?", request, re.I)
    if sheets_m:
        sheet_names = [sheets_m.group(1), sheets_m.group(2)]
        xlsx_nodes = [n for n in graph.nodes if ".xlsx" in n.goal.lower() or any(hasattr(c, "path") and ".xlsx" in str(getattr(c, "path", "")).lower() for c in n.success_criteria)]
        if xlsx_nodes:
            target_node = xlsx_nodes[-1]
            target_path = next((getattr(c, "path") for c in target_node.success_criteria if hasattr(c, "path") and ".xlsx" in str(getattr(c, "path", "")).lower()), None)
            if target_path and not any(isinstance(c, XlsxHasSheets) for c in target_node.success_criteria):
                target_node.success_criteria.append(XlsxHasSheets(type="xlsx_has_sheets", path=target_path, sheets=sheet_names))
                notes.append(f"{target_node.id}: must have sheets {sheet_names}")

    # 4. Docx content: "stating the sum", literal quoted strings, SHA-256
    docx_nodes = [n for n in graph.nodes if ".docx" in n.goal.lower() or any(hasattr(c, "path") and ".docx" in str(getattr(c, "path", "")).lower() for c in n.success_criteria)]
    if docx_nodes:
        target_node = docx_nodes[-1]
        target_path = next((getattr(c, "path") for c in target_node.success_criteria if hasattr(c, "path") and ".docx" in str(getattr(c, "path", "")).lower()), None)
        if not target_path:
            m_d = re.search(r"\b([\w\-]+\.docx)\b", request, re.I)
            if m_d:
                target_path = _full_path(m_d.group(1))
        if target_path:
            if re.search(r"stating (?:the )?sum\b", request, re.I):
                if not any(isinstance(c, DocxContains) and "sum" in c.text.lower() for c in target_node.success_criteria):
                    target_node.success_criteria.append(DocxContains(type="docx_contains", path=target_path, text="sum"))
                    notes.append(f"{target_node.id}: must state sum in docx")
            if re.search(r"\bSHA-?256\b", request, re.I) and "docx" in request.lower():
                if not any(isinstance(c, DocxContains) and "sha" in c.text.lower() for c in target_node.success_criteria):
                    target_node.success_criteria.append(DocxContains(type="docx_contains", path=target_path, text="SHA-256"))
                    notes.append(f"{target_node.id}: must mention SHA-256 in docx")
            if re.search(r"computer name\b", request, re.I) and "docx" in request.lower():
                host = os.environ.get("COMPUTERNAME", "")
                if host and not any(isinstance(c, DocxContains) and host.lower() in c.text.lower() for c in target_node.success_criteria):
                    target_node.success_criteria.append(DocxContains(type="docx_contains", path=target_path, text=host))
                    notes.append(f"{target_node.id}: must contain computer name {host} in docx")
            if "notes.md" in request.lower() and "docx" in request.lower():
                notes_p = _full_path("notes.md")
                real_notes = os.path.abspath(os.path.expanduser(notes_p))
                if os.path.isfile(real_notes):
                    txt = open(real_notes, encoding="utf-8", errors="ignore").read()
                    for h_m in re.finditer(r"^##\s+(.+)$", txt, re.M):
                        head = h_m.group(1).strip()
                        if not any(isinstance(c, DocxContains) and head.lower() in c.text.lower() for c in target_node.success_criteria):
                            target_node.success_criteria.append(DocxContains(type="docx_contains", path=target_path, text=head))
                            notes.append(f"{target_node.id}: must contain heading {head} in docx")
            for qm in re.finditer(r"['\"]([^'\"]{3,60})['\"]", request):
                qtext = qm.group(1).strip()
                if not any(isinstance(c, DocxContains) and c.text == qtext for c in target_node.success_criteria):
                    target_node.success_criteria.append(DocxContains(type="docx_contains", path=target_path, text=qtext))
                    notes.append(f"{target_node.id}: must contain {qtext!r} in docx")

    # 5. "subfolder named X" or "copy into folder X"
    sub_m = re.search(r"(?:subfolder\s+named|into\s+(?:a\s+)?folder\s+named)\s+([\w\-]+)", request, re.I)
    if sub_m:
        sub_name = sub_m.group(1)
        sub_folder = (folders[-1][1] if folders else "~") + "/" + sub_name
        dest_nodes = [n for n in graph.nodes if sub_name.lower() in n.goal.lower() or "move" in n.goal.lower() or "copy" in n.goal.lower()]
        if dest_nodes:
            target_node = dest_nodes[-1]
            if not any(isinstance(c, FolderHasFiles) and sub_name in c.path.lower() for c in target_node.success_criteria):
                target_node.success_criteria.append(FolderHasFiles(type="folder_has_files", path=sub_folder, min_count=1))
                notes.append(f"{target_node.id}: must have files in {sub_folder}")

    return notes


def fallback_graph(request: str) -> TaskGraph:
    """Single node: the whole request through the existing loop. With no typed checks, the
    node counts as verified only if the orchestrator's own strict verifier accepted completion;
    reports flag the plan as a fallback."""
    return TaskGraph.model_validate({
        "goal": request, "fallback": True,
        "nodes": [{"id": "whole_task", "goal": request if len(request.strip()) >= 5 else f"Do this: {request}",
                   "max_attempts": 1}],
    })


class Planner:
    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient()

    async def _ask(self, messages) -> str:
        return await self.llm.complete_json(messages, TaskGraph.model_json_schema(), name="task_graph",
                                            max_tokens=3000)

    async def plan(self, request: str) -> Tuple[TaskGraph, dict]:
        """Returns (graph, info) where info records attempts, errors and whether it fell back."""
        info = {"attempts": 0, "errors": [], "fallback": False}
        messages = [{"role": "system", "content": _system_prompt(_tool_lines())},
                    {"role": "user", "content": f"Request: {request}\nReturn the TaskGraph JSON."}]
        for attempt in range(2):  # first try + one repair
            info["attempts"] += 1
            try:
                raw = await self._ask(messages)
            except Exception as e:
                info["errors"].append(f"LLM error: {e}")
                break
            try:
                graph = TaskGraph.model_validate(json.loads(raw))
                pin_requested_paths(request, graph)
                derive_required_checks(request, graph)
                return graph, info
            except (ValidationError, ValueError) as e:
                err = str(e)[:1500]
                info["errors"].append(err)
                messages += [{"role": "assistant", "content": raw[:3000]},
                             {"role": "user", "content": f"That plan is invalid:\n{err}\nReturn a corrected TaskGraph JSON."}]
        info["fallback"] = True
        return fallback_graph(request), info
