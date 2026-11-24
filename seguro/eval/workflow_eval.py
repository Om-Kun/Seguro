"""
Runs `workflow: true` eval tasks through the workflow engine. Checkers (written in tasks.yaml by
hand, independent of the planner's own success criteria) get `run`, `events` and helper functions.
Optional `kill_after_verified: N` triggers the killswitch after N nodes verify, then resumes.
"""
import asyncio
import json
import os
import time

from seguro.core.events import bus, read_events
from seguro.safety.killswitch import killswitch


def _check_helpers():
    import openpyxl
    from docx import Document
    from pptx import Presentation

    def xlsx_rows(path, sheet=None):
        wb = openpyxl.load_workbook(os.path.expanduser(path), data_only=True)
        ws = wb[sheet] if sheet else wb.active
        return [list(r) for r in ws.iter_rows(values_only=True) if any(v not in (None, "") for v in r)]

    def xlsx_sheets(path):
        return openpyxl.load_workbook(os.path.expanduser(path)).sheetnames

    def xlsx_charts(path):
        wb = openpyxl.load_workbook(os.path.expanduser(path))
        return sum(len(ws._charts) for ws in wb.worksheets)

    def docx_text(path):
        d = Document(os.path.expanduser(path))
        return "\n".join(p.text for p in d.paragraphs) + "\n" + "\n".join(
            c.text for t in d.tables for r in t.rows for c in r.cells)

    def docx_images(path):
        return len(Document(os.path.expanduser(path)).inline_shapes)

    def pptx_info(path):
        from pptx.enum.shapes import MSO_SHAPE_TYPE
        prs = Presentation(os.path.expanduser(path))
        pics = sum(1 for s in prs.slides for sh in s.shapes if sh.shape_type == MSO_SHAPE_TYPE.PICTURE)
        return len(prs.slides), pics

    def gmail_draft(subject, with_attachment=True):
        from seguro.tools.gmail import gmail_draft_check
        ok, _ = gmail_draft_check({"subject_contains": subject, "with_attachment": with_attachment}, None, None)
        return ok

    def find_file(root, *names):
        """First existing file under root (recursive) whose name matches any of names (case-insensitive)."""
        root = os.path.expanduser(root)
        want = {n.lower() for n in names}
        for d, _, files in os.walk(root):
            for f in files:
                if f.lower() in want:
                    return os.path.join(d, f)
        return None

    return dict(xlsx_rows=xlsx_rows, xlsx_sheets=xlsx_sheets, xlsx_charts=xlsx_charts, docx_text=docx_text,
                docx_images=docx_images, pptx_info=pptx_info, gmail_draft=gmail_draft, find_file=find_file)


async def run_workflow_task(task: dict, mode: str, setup_globals: dict) -> dict:
    from seguro.core.workflow.runner import run_workflow, resume_workflow
    t0 = time.time()
    kill_after = task.get("kill_after_verified")
    token = None
    resumed_id = None
    if kill_after:
        verified = {"n": 0}

        def on_event(ev):
            if ev.type == "node_verified":
                verified["n"] += 1
                if verified["n"] == kill_after:
                    killswitch.trigger()
        token = bus.subscribe(on_event, {"node_verified"})
    try:
        run = await asyncio.wait_for(run_workflow(task["prompt"], mode=mode), timeout=task.get("timeout_s", 900))
        if kill_after:
            bus.unsubscribe(token)
            token = None
            if run.status != "interrupted":
                raise RuntimeError(f"expected an interrupted workflow, got {run.status}")
            killswitch.reset()
            resumed_id = run.id
            run = await asyncio.wait_for(resume_workflow(run.id, mode), timeout=task.get("timeout_s", 900))
    except Exception as e:
        return {"status": "FAIL", "error": f"{type(e).__name__}: {e}", "elapsed_s": round(time.time() - t0, 2),
                "agent_status": None, "steps": 0, "rungs": []}
    finally:
        if token:
            bus.unsubscribe(token)
        killswitch.reset()

    elapsed = time.time() - t0
    events = read_events(os.path.join(run.dir, "events.jsonl"))
    check_globals = dict(setup_globals, run=run, events=events, json=json, time=time, **_check_helpers())
    passed, err = True, None
    try:
        exec(task["check"], check_globals)
    except AssertionError as e:
        passed, err = False, f"Assertion failed: {e}" if str(e) else "Assertion failed"
    except Exception as e:
        passed, err = False, f"{type(e).__name__}: {e}"

    node_times = {}
    starts = {}
    for e in events:
        if e["type"] == "node_started":
            starts.setdefault(e["node_id"], e["ts"])
        elif e["type"] in ("node_verified", "node_failed") and e["node_id"] in starts:
            node_times[e["node_id"]] = round(e["ts"] - starts[e["node_id"]], 1)
    tools = [e["data"]["tool"] for e in events if e["type"] == "tool_call"]
    return {
        "status": "PASS" if passed else "FAIL",
        "agent_status": "success" if run.status == "done" else run.status,
        "evidence": run.report, "error": err, "elapsed_s": round(elapsed, 2),
        "steps": len(tools), "rungs": tools, "workflow_id": run.id, "resumed_from": resumed_id,
        "plan_source": run.plan_info.get("source"), "plan_fallback": bool(run.plan_info.get("fallback")),
        "nodes": len(run.graph.nodes),
        "nodes_verified": sum(1 for s in run.states.values() if s.status == "verified"),
        "replans": run.replans, "escalated_nodes": sum(1 for s in run.states.values() if s.escalated),
        "node_times_s": node_times,
        "confirmations": sum(1 for e in events if e["type"] == "needs_confirmation"),
    }
