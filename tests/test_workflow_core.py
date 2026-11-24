"""Unit tests for workflow models, blackboard, checks, checkpoint and the event bus."""
import os
import sys
import json
import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from seguro.core.events import EventBus, read_events, current_context
from seguro.core.workflow.models import TaskGraph, Node, CHECK_TYPES
from seguro.core.workflow.blackboard import Blackboard, UnresolvedRef, OutputTypeError
from seguro.core.workflow.checks import run_checks
from seguro.core.workflow import checkpoint


def _graph(**overrides):
    g = {
        "goal": "xlsx then docx",
        "nodes": [
            {"id": "make_xlsx", "goal": "Create the spreadsheet", "expected_output": {"file_path": "path"}},
            {"id": "make_docx", "goal": "Write a report using the sheet",
             "inputs": {"sheet": "{{make_xlsx.file_path}}"}, "expected_output": {"file_path": "path"}},
        ],
    }
    g.update(overrides)
    return g


def test_valid_graph_infers_dependencies_and_order():
    tg = TaskGraph.model_validate(_graph())
    assert tg.node("make_docx").depends_on == ["make_xlsx"]
    assert tg.topo_order() == ["make_xlsx", "make_docx"]
    # declared outputs automatically get a presence check
    assert any(c.type == "output_present" for c in tg.node("make_xlsx").success_criteria)


def test_bad_ref_and_unknown_dep_rejected():
    g = _graph()
    g["nodes"][1]["inputs"] = {"sheet": "{{make_xlsx.nope}}"}
    with pytest.raises(ValidationError, match="declares outputs"):
        TaskGraph.model_validate(g)
    g = _graph()
    g["nodes"][0]["depends_on"] = ["Excel"]  # what the 8B model produced in the probe
    with pytest.raises(ValidationError):
        TaskGraph.model_validate(g)


def test_cycle_rejected():
    g = _graph()
    g["nodes"][0]["depends_on"] = ["make_docx"]
    with pytest.raises(ValidationError, match="cycle"):
        TaskGraph.model_validate(g)


def test_unverifiable_node_rejected_and_unknown_check_type_rejected():
    with pytest.raises(ValidationError, match="never be verified"):
        TaskGraph.model_validate({"goal": "x", "nodes": [{"id": "a", "goal": "Do something vague"}]})
    with pytest.raises(ValidationError):
        Node.model_validate({"id": "a", "goal": "Run code check", "success_criteria": [{"type": "python_eval", "code": "1"}]})
    assert "xlsx_has_chart" in CHECK_TYPES and "python_eval" not in CHECK_TYPES


def test_blackboard_types_refs_and_spill(tmp_path):
    bb = Blackboard(str(tmp_path))
    f = tmp_path / "a.txt"; f.write_text("x")
    bb.put("n1", "file_path", str(f), "path")
    with pytest.raises(OutputTypeError):
        bb.put("n1", "ghost", str(tmp_path / "missing.txt"), "path")
    big = [[i, "row" * 50] for i in range(200)]
    bb.put("n1", "table", big, "table")
    assert "ref" in bb.entries["n1"]["table"] and bb.get("n1", "table") == big
    assert bb.resolve("{{n1.table}}") == big            # whole-string ref keeps the type
    assert bb.resolve("see {{n1.file_path}}").endswith("a.txt")
    with pytest.raises(UnresolvedRef):
        bb.resolve("{{n9.anything}}")                  # never silently ''
    bb2 = Blackboard.from_dict(json.loads(json.dumps(bb.to_dict())))
    assert bb2.get("n1", "table") == big


def test_checks_pass_and_fail_on_real_files(tmp_path):
    import openpyxl
    from openpyxl.chart import BarChart, Reference
    p = tmp_path / "s.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active
    for r in [["k", "v"], ["a", 1], ["b", 2]]:
        ws.append(r)
    ch = BarChart(); ch.add_data(Reference(ws, min_col=2, min_row=1, max_row=3), titles_from_data=True); ws.add_chart(ch, "D2")
    wb.save(p)
    bb = Blackboard(str(tmp_path)); bb.put("n1", "file_path", str(p), "path")
    node = Node.model_validate({"id": "n1", "goal": "make sheet", "expected_output": {"file_path": "path"},
        "success_criteria": [{"type": "xlsx_min_rows", "path": "{{n1.file_path}}", "min_rows": 2},
                             {"type": "xlsx_numeric_column", "path": "{{n1.file_path}}", "column": 2, "min_count": 2},
                             {"type": "xlsx_has_chart", "path": "{{n1.file_path}}"}]})
    ok, res = run_checks(node.success_criteria, "n1", bb)
    assert ok, res
    node.success_criteria[0].min_rows = 5
    ok, res = run_checks(node.success_criteria, "n1", bb)
    assert not ok and not res[0]["ok"]


def test_missing_output_and_unimplemented_checker_fail(tmp_path):
    bb = Blackboard(str(tmp_path))
    node = Node.model_validate({"id": "n1", "goal": "draft email", "expected_output": {"draft": "text"},
                                "success_criteria": [{"type": "gmail_draft_exists", "subject_contains": "x"}]})
    ok, res = run_checks(node.success_criteria, "n1", bb)
    assert not ok and all(not r["ok"] for r in res)


def test_checkpoint_roundtrip_atomic(tmp_path):
    checkpoint.save("wf1", {"status": "running", "graph": {"goal": "g"}}, root=str(tmp_path))
    assert checkpoint.load("wf1", root=str(tmp_path))["status"] == "running"
    assert not os.path.exists(tmp_path / "wf1" / "state.json.tmp")
    assert checkpoint.list_workflows(root=str(tmp_path))[0]["goal"] == "g"


def test_event_bus_sink_context_and_bad_subscriber(tmp_path):
    bus = EventBus()
    seen = []
    bus.subscribe(lambda e: seen.append(e.type), {"node_started"})
    bus.subscribe(lambda e: 1 / 0)  # must not break emitters
    sink = tmp_path / "wf" / "events.jsonl"
    bus.add_sink("wf", str(sink))
    tok = current_context.set({"workflow_id": "wf", "node_id": "n1"})
    try:
        bus.emit("node_started", goal="g")
        bus.emit("tool_call", tool="x")
    finally:
        current_context.reset(tok)
    assert seen == ["node_started"]
    evs = read_events(str(sink))
    assert [e["type"] for e in evs] == ["node_started", "tool_call"] and evs[1]["node_id"] == "n1"
    with pytest.raises(ValueError):
        bus.emit("made_up_event")
