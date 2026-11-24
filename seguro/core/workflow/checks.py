"""Runs typed success checks in code. A node is verified only when all of them pass."""
import fnmatch
import os
from typing import Callable, Dict, List, Tuple

from seguro.core.workflow.blackboard import Blackboard, UnresolvedRef


def _path(p: str) -> str:
    return os.path.abspath(os.path.expanduser(str(p)))


def _sheet(path, sheet):
    import openpyxl
    wb = openpyxl.load_workbook(_path(path))
    return wb[sheet] if sheet else wb.active


def _file_exists(c, node_id, bb):
    from seguro.core.workflow.blackboard import validate_file_content, OutputTypeError
    p = _path(c["path"])
    if not os.path.exists(p):
        return False, f"{p} missing"
    try:
        validate_file_content(p)
    except OutputTypeError as e:
        return False, str(e)
    return True, f"{p} exists and is a valid {os.path.splitext(p)[1] or 'file'}"


def _file_contains(c, node_id, bb):
    p = _path(c["path"])
    if not os.path.isfile(p):
        return False, f"{p} missing"
    ok = c["text"] in open(p, encoding="utf-8", errors="replace").read()
    return ok, f"{'found' if ok else 'did not find'} {c['text']!r} in {p}"


def _xlsx_rows(ws):
    return [r for r in ws.iter_rows(values_only=True) if any(v not in (None, "") for v in r)]


def _xlsx_min_rows(c, node_id, bb):
    rows = _xlsx_rows(_sheet(c["path"], c.get("sheet")))
    data_rows = max(0, len(rows) - 1)  # first non-empty row is the header
    return data_rows >= c["min_rows"], f"{data_rows} data rows (need ≥{c['min_rows']})"


def _xlsx_numeric_column(c, node_id, bb):
    rows = _xlsx_rows(_sheet(c["path"], c.get("sheet")))[1:]
    col = c["column"] - 1
    nums = [r[col] for r in rows if len(r) > col and isinstance(r[col], (int, float)) and not isinstance(r[col], bool)]
    return len(nums) >= c["min_count"], f"{len(nums)} numeric values in column {c['column']}"


def _xlsx_has_chart(c, node_id, bb):
    n = len(_sheet(c["path"], c.get("sheet"))._charts)
    return n >= 1, f"{n} chart(s)"


def _xlsx_has_columns(c, node_id, bb):
    p = _path(c["path"])
    if not os.path.isfile(p):
        return False, f"{p} missing"
    rows = _xlsx_rows(_sheet(c["path"], c.get("sheet")))
    if not rows:
        return False, "spreadsheet is empty"
    header_row = [str(cell).strip().lower() for cell in rows[0] if cell is not None]
    required = [str(col).strip().lower() for col in c["columns"]]
    missing = [req for req in required if not any(req == h or req in h for h in header_row)]
    ok = len(missing) == 0
    return ok, f"columns {c['columns']}: {'all present' if ok else 'missing ' + str(missing)}"


def _xlsx_has_sheets(c, node_id, bb):
    import openpyxl
    p = _path(c["path"])
    if not os.path.isfile(p):
        return False, f"{p} missing"
    wb = openpyxl.load_workbook(p)
    actual_sheets = [s.strip().lower() for s in wb.sheetnames]
    required = [s.strip().lower() for s in c["sheets"]]
    missing = [req for req in required if not any(req == s or req in s for s in actual_sheets)]
    ok = len(missing) == 0
    return ok, f"sheets {c['sheets']}: {'all present' if ok else 'missing ' + str(missing)}"


def _docx_contains(c, node_id, bb):
    from docx import Document
    doc = Document(_path(c["path"]))
    text = "\n".join(p.text for p in doc.paragraphs)
    text += "\n".join(cell.text for t in doc.tables for row in t.rows for cell in row.cells)
    ok = c["text"].lower() in text.lower()
    return ok, f"{'found' if ok else 'did not find'} {c['text']!r}"


def _docx_has_image(c, node_id, bb):
    from docx import Document
    doc = Document(_path(c["path"]))
    n = len(doc.inline_shapes)
    return n >= c["min_images"], f"{n} inline image(s)"


def _pptx_slide_count(c, node_id, bb):
    from pptx import Presentation
    n = len(Presentation(_path(c["path"])).slides)
    ok = n >= c["min"] and (c.get("max") is None or n <= c["max"])
    return ok, f"{n} slide(s)"


def _pptx_has_image(c, node_id, bb):
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    n = sum(1 for s in Presentation(_path(c["path"])).slides for sh in s.shapes
            if sh.shape_type == MSO_SHAPE_TYPE.PICTURE)
    return n >= 1, f"{n} picture(s)"


def _folder_has_files(c, node_id, bb):
    p = _path(c["path"])
    if not os.path.isdir(p):
        return False, f"{p} is not a folder"
    n = len([f for f in os.listdir(p) if os.path.isfile(os.path.join(p, f)) and fnmatch.fnmatch(f, c["pattern"])])
    return n >= c["min_count"], f"{n} file(s) matching {c['pattern']}"


def _window_title_contains(c, node_id, bb):
    from seguro.tools.windows import windows_list
    titles = [w["title"] for w in windows_list()]
    hit = [t for t in titles if c["text"].lower() in t.lower()]
    return bool(hit), (f"window '{hit[0]}'" if hit else f"no window title contains {c['text']!r}")


def _http_ok(c, node_id, bb):
    import httpx
    r = httpx.get(c["url"], timeout=10, follow_redirects=True)
    ok = r.status_code == 200 and (not c.get("contains") or c["contains"] in r.text)
    return ok, f"HTTP {r.status_code}{'' if not c.get('contains') else ', contains=' + str(c['contains'] in r.text)}"


def _output_present(c, node_id, bb: Blackboard):
    ok = bb.has(node_id, c["key"])
    return ok, f"output '{c['key']}' {'published' if ok else 'was never published (call node_output)'}"


CHECKERS: Dict[str, Callable] = {
    "file_exists": _file_exists, "file_contains": _file_contains, "xlsx_min_rows": _xlsx_min_rows,
    "xlsx_numeric_column": _xlsx_numeric_column, "xlsx_has_chart": _xlsx_has_chart,
    "xlsx_has_columns": _xlsx_has_columns, "xlsx_has_sheets": _xlsx_has_sheets,
    "docx_contains": _docx_contains, "docx_has_image": _docx_has_image,
    "pptx_slide_count": _pptx_slide_count, "pptx_has_image": _pptx_has_image,
    "folder_has_files": _folder_has_files, "window_title_contains": _window_title_contains,
    "http_ok": _http_ok, "output_present": _output_present,
}


def register_checker(check_type: str, fn: Callable):
    """Lets tool modules (e.g. Gmail) add their checker without this module importing them."""
    CHECKERS[check_type] = fn


def run_checks(checks: List, node_id: str, bb: Blackboard) -> Tuple[bool, List[dict]]:
    results = []
    for chk in checks:
        c = chk.model_dump() if hasattr(chk, "model_dump") else dict(chk)
        ctype = c["type"]
        try:
            resolved = {k: (bb.resolve(v) if isinstance(v, str) and k != "type" else v) for k, v in c.items()}
            fn = CHECKERS.get(ctype)
            if fn is None:
                ok, detail = False, f"no checker implemented for '{ctype}'"
            else:
                ok, detail = fn(resolved, node_id, bb)
        except UnresolvedRef as e:
            ok, detail = False, f"unresolved reference: {e}"
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        results.append({"type": ctype, "ok": bool(ok), "detail": detail})
    return all(r["ok"] for r in results) and bool(results), results
