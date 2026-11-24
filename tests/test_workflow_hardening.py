"""Regressions for issues found in the first live workflow runs."""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import seguro.core.orchestrator  # noqa: F401  (registers tools)
from seguro.tools.registry import registry
from seguro.core.state import TaskState
from seguro.core.verifier import verifier
from seguro.core.workflow.blackboard import Blackboard, OutputTypeError
from seguro.core.workflow.executor import autopublish_from_verified
from seguro.core.workflow.models import Node, TaskGraph
from seguro.core.workflow.replanner import merge
from seguro.core.workflow.node_context import workflow_started_at
from seguro.core.workflow.gates import classify


@pytest.mark.asyncio
async def test_string_booleans_and_nested_lists_are_coerced(tmp_path):
    from seguro.tools.registry import _coerce_args
    fn = registry._tools["word_create_document"]
    args, err = _coerce_args(fn, registry._schemas["word_create_document"],
                             {"title": "t", "paragraphs": "['a']", "images": ['["x.png", "y.png"]'], "open_after": "False"})
    assert err is None and args["open_after"] is False and args["images"] == ["x.png", "y.png"]


def test_fake_png_is_rejected_everywhere(tmp_path):
    fake = tmp_path / "chart.png"
    fake.write_text("not a png")
    with pytest.raises(OutputTypeError):
        Blackboard(str(tmp_path)).put("n", "image", str(fake), "path")
    v = verifier.verify_action("files_write", {"path": str(fake)}, {"ok": True, "data": {"ok": True, "path": str(fake)}})
    assert v["verified"] is False and "content" in v["evidence"]


@pytest.mark.asyncio
async def test_chart_png_makes_a_real_image():
    from seguro.tools.charts import chart_png
    from PIL import Image
    p = os.path.join(os.path.expanduser("~"), "Desktop", "seguro_test_chart.png")
    try:
        r = chart_png([["Source", "Title"], ["BBC", "a"], ["BBC", "b"], ["Wired", "c"]], p, count_by="Source")
        assert r["ok"] and r["values"] == [2, 1]
        Image.open(p).verify()
        assert chart_png([["k", "v"], ["a", "x"]], p)["ok"] is False   # non-numeric -> honest error
    finally:
        if os.path.exists(p):
            os.remove(p)


def test_replan_may_not_drop_checks():
    g = TaskGraph.model_validate({"goal": "g", "nodes": [
        {"id": "doc", "goal": "write doc with chart", "expected_output": {"file_path": "path"},
         "success_criteria": [{"type": "docx_has_image", "path": "{{doc.file_path}}"}]}]})
    weaker = [Node.model_validate({"id": "doc_v2", "goal": "write doc", "expected_output": {"file_path": "path"},
                                   "success_criteria": [{"type": "docx_contains", "path": "{{doc_v2.file_path}}", "text": "x"}]})]
    with pytest.raises(ValueError, match="drops required checks"):
        merge(g, ["doc"], weaker)


def test_autopublish_uses_only_verified_results(tmp_path):
    xl = tmp_path / "a.xlsx"
    import openpyxl
    openpyxl.Workbook().save(xl)
    node = Node.model_validate({"id": "s", "goal": "make sheet", "expected_output": {"file_path": "path", "table": "table"}})
    st = TaskState(request="r")
    st.record_step("excel_create_table_and_chart", {}, {"ok": True, "data": {"path": str(xl), "table": [["k"], [1]]}},
                   {"verified": True, "kind": "act"})
    st.record_step("files_write", {}, {"ok": True, "data": {"path": "C:/fake.xlsx"}}, {"verified": False, "kind": "act"})
    values = {}
    assert autopublish_from_verified(node, st, values) == {"file_path": str(xl), "table": [["k"], [1]]}


def test_overwrite_gate_spares_own_artifacts(tmp_path):
    import time
    p = os.path.join(os.path.expanduser("~"), "Desktop", "seguro_gate_probe.txt")
    try:
        open(p, "w").write("x")
        tok = workflow_started_at.set(time.time() + 3600)   # file predates the workflow -> user's file
        assert classify("files_write", {"path": p})
        workflow_started_at.reset(tok)
        tok = workflow_started_at.set(time.time() - 60)     # created during the workflow -> own artifact
        assert classify("files_write", {"path": p}) is None
        workflow_started_at.reset(tok)
    finally:
        os.remove(p)


def test_requested_output_paths_are_pinned(tmp_path):
    from seguro.core.workflow.planner import pin_requested_paths
    req = ("Read sales.csv in ~/Desktop/zz_no_such_dir, create sales.xlsx in the same folder with a chart, "
           "and write sales_summary.docx in that folder")
    g = TaskGraph.model_validate({"goal": req, "nodes": [
        {"id": "make_xlsx", "goal": "Create sales.xlsx with the data", "expected_output": {"file_path": "path"}},
        {"id": "write_doc", "goal": "Write sales_summary.docx", "expected_output": {"file_path": "path"}}]})
    notes = pin_requested_paths(req, g)
    pinned = {n.id: [c.path for c in n.success_criteria if c.type == "file_exists"] for n in g.nodes}
    assert pinned["make_xlsx"] == ["~/Desktop/zz_no_such_dir/sales.xlsx"], notes
    assert pinned["write_doc"] == ["~/Desktop/zz_no_such_dir/sales_summary.docx"]
    assert "~/Desktop/zz_no_such_dir/sales.xlsx" in g.node("make_xlsx").goal


def test_named_downloads_folder_and_existing_inputs():
    from seguro.core.workflow.planner import pin_requested_paths, _folders_with_positions
    req = "Go through the folder seguro_zz_sort in my Downloads and make sort_summary.xlsx in that same folder"
    assert _folders_with_positions(req)[0][1] == "~/Downloads/seguro_zz_sort"
    g = TaskGraph.model_validate({"goal": req, "nodes": [
        {"id": "summary", "goal": "Make sort_summary.xlsx", "expected_output": {"file_path": "path"}}]})
    pin_requested_paths(req, g)
    assert [c.path for c in g.node("summary").success_criteria if c.type == "file_exists"] == \
        ["~/Downloads/seguro_zz_sort/sort_summary.xlsx"]


def test_python_and_json_slips_are_normalized(tmp_path):
    from seguro.tools.pyexec import run_python
    from seguro.tools.files import files_write
    out = os.path.join(os.path.expanduser("~"), "Desktop", "seguro_t_sq.json")
    try:
        code = ('python -c "import json; data = [i*i for i in range(1, 4)]; '
                "with open('~/Desktop/seguro_t_sq.json', 'w') as f: json.dump(data, f)\"")
        r = run_python(code)
        assert r["returncode"] == 0, r
        assert json.load(open(out)) == [1, 4, 9]
        w = files_write(out, "[{'n': 1, 'sq': 1}]")
        assert w["ok"] and json.load(open(out)) == [{"n": 1, "sq": 1}]
    finally:
        if os.path.exists(out):
            os.remove(out)
    assert Blackboard(str(tmp_path)).put("n", "j", "{'a': [1, 2]}", "json") == {"a": [1, 2]}


def test_xlsx_has_columns_and_sheets_checks(tmp_path):
    import openpyxl
    from seguro.core.workflow.checks import run_checks
    from seguro.core.workflow.models import XlsxHasColumns, XlsxHasSheets

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Invoice ID", "Amount", "Vendor"])
    ws.append(["INV-001", 100.5, "Acme Corp"])
    wb.create_sheet("Details")
    p = str(tmp_path / "test_invoice.xlsx")
    wb.save(p)

    bb = Blackboard(str(tmp_path))

    # Positive: columns and sheets present
    checks_pos = [
        XlsxHasColumns(type="xlsx_has_columns", path=p, columns=["Invoice ID", "Amount"]),
        XlsxHasSheets(type="xlsx_has_sheets", path=p, sheets=["Summary", "Details"]),
    ]
    ok, res = run_checks(checks_pos, "node1", bb)
    assert ok is True
    assert all(r["ok"] for r in res)

    # Negative: missing column
    checks_bad_col = [
        XlsxHasColumns(type="xlsx_has_columns", path=p, columns=["NonExistentCol"])
    ]
    ok, res = run_checks(checks_bad_col, "node1", bb)
    assert ok is False
    assert res[0]["ok"] is False

    # Negative: missing sheet
    checks_bad_sheet = [
        XlsxHasSheets(type="xlsx_has_sheets", path=p, sheets=["NonExistentSheet"])
    ]
    ok, res = run_checks(checks_bad_sheet, "node1", bb)
    assert ok is False
    assert res[0]["ok"] is False


def test_derive_required_checks_extracts_typed_checks():
    from seguro.core.workflow.planner import derive_required_checks
    from seguro.core.workflow.models import TaskGraph

    req = (
        "Read invoice data and create invoice_summary.xlsx with columns Invoice_ID, Amount and Vendor. "
        "Also create presentation.pptx with 3 slides and report.docx stating the sum of amounts. "
        "Move everything into a folder named Archive."
    )
    g = TaskGraph.model_validate({
        "goal": req,
        "nodes": [
            {"id": "node_xl", "goal": "Generate invoice_summary.xlsx", "expected_output": {"file_path": "path"}},
            {"id": "node_ppt", "goal": "Generate presentation.pptx", "expected_output": {"file_path": "path"}},
            {"id": "node_doc", "goal": "Generate report.docx", "expected_output": {"file_path": "path"}},
            {"id": "node_mv", "goal": "Move to Archive folder", "expected_output": {"dest": "path"}},
        ]
    })

    notes = derive_required_checks(req, g)
    assert len(notes) > 0

    xl_node = g.node("node_xl")
    ppt_node = g.node("node_ppt")
    doc_node = g.node("node_doc")
    mv_node = g.node("node_mv")

    # xlsx should have columns and min_rows
    assert any(c.type == "xlsx_has_columns" for c in xl_node.success_criteria)
    assert any(c.type == "xlsx_min_rows" for c in xl_node.success_criteria)

    # pptx should have slide count
    assert any(c.type == "pptx_slide_count" for c in ppt_node.success_criteria)

    # docx should state sum
    assert any(c.type == "docx_contains" and "sum" in c.text.lower() for c in doc_node.success_criteria)

    # folder should have files
    assert any(c.type == "folder_has_files" for c in mv_node.success_criteria)

