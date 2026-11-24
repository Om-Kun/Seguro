"""
Runs a TaskGraph. Topological; GUI nodes strictly one at a time (one mouse/keyboard), non-GUI
nodes concurrently (bounded). Each node wraps the existing orchestrator loop, scoped to its goal.
A node is verified ONLY when its typed checks pass in code.
"""
import asyncio
import json
import time
import uuid
from typing import Dict, Optional

from seguro.core.events import bus, current_context
from seguro.core.workflow import checkpoint
from seguro.core.workflow.blackboard import Blackboard, UnresolvedRef, OutputTypeError
from seguro.core.workflow.checks import run_checks
from seguro.core.workflow.gates import broker
from seguro.core.workflow.models import TaskGraph, Node, NodeState
from seguro.core.workflow.node_context import current_node_outputs, workflow_started_at
from seguro.core.rungs import determine_rung
from seguro.safety.killswitch import killswitch

TERMINAL = {"verified", "failed", "skipped"}


class WorkflowRun:
    """All mutable state of one workflow; serialisable to a checkpoint."""

    def __init__(self, graph: TaskGraph, request: str, mode: str, workflow_id: Optional[str] = None,
                 root: Optional[str] = None, plan_info: Optional[dict] = None):
        self.id = workflow_id or time.strftime("wf_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:4]
        self.root = root
        self.dir = checkpoint.workflow_dir(self.id, root)
        self.graph = graph
        self.request = request
        self.mode = mode
        self.states: Dict[str, NodeState] = {n.id: NodeState() for n in graph.nodes}
        self.bb = Blackboard(self.dir)
        self.replans = 0
        self.status = "running"
        self.plan_info = plan_info or {}
        self.report = ""
        self.created_at = time.time()

    def to_dict(self) -> dict:
        return {"id": self.id, "request": self.request, "mode": self.mode, "status": self.status,
                "graph": self.graph.model_dump(), "states": {k: v.model_dump() for k, v in self.states.items()},
                "blackboard": self.bb.to_dict(), "replans": self.replans, "plan_info": self.plan_info,
                "report": self.report, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, d: dict, root: Optional[str] = None) -> "WorkflowRun":
        run = cls(TaskGraph.model_validate(d["graph"]), d["request"], d["mode"], d["id"], root, d.get("plan_info"))
        run.states = {k: NodeState.model_validate(v) for k, v in d["states"].items()}
        run.bb = Blackboard.from_dict(d["blackboard"])
        run.replans = d.get("replans", 0)
        run.status = d.get("status", "running")
        run.created_at = d.get("created_at", time.time())
        return run

    def save(self):
        checkpoint.save(self.id, self.to_dict(), self.root)


def _node_prompt(run: WorkflowRun, node: Node, inputs: dict, feedback: str) -> tuple:
    files_dir = f"{run.dir}\\files"
    lines = [f"This is ONE step of a larger workflow. Overall goal: {run.graph.goal}",
             f"Your step: {node.goal}"]
    if inputs:
        lines.append("Inputs (already produced by earlier steps):")
        for k, v in inputs.items():
            text = v if isinstance(v, str) else json.dumps(v, default=str)
            lines.append(f"- {k} = {text[:1500]}")
    if node.expected_output:
        lines.append("Required outputs. Publish each with node_output(key, value) before task_complete:")
        for k, kind in node.expected_output.items():
            lines.append(f"- {k} ({kind}{': an absolute file path that exists' if kind == 'path' else ''}"
                         f"{': JSON list of rows' if kind == 'table' else ''})")
    if feedback:
        lines.append(f"A previous attempt did not pass verification:\n{feedback}\nFix exactly that.")
    system_extra = (f"WORKFLOW STEP CONTEXT: preferred approach (rung): {node.preferred_rung}. "
                    f"If the step names a folder or file path, pass that FULL path (e.g. '~/Desktop/x/report.xlsx') as "
                    f"the filename/path argument of every tool; a bare file name would land on the Desktop. "
                    f"Save intermediate files under '{files_dir}' unless the step names another location. "
                    f"Only do this step; later steps are handled separately.")
    return "\n".join(lines), system_extra


class Executor:
    def __init__(self, orchestrator=None, replanner=None, max_parallel: int = 3, max_replans: int = 3,
                 node_max_steps: int = 12, escalation_llm=None):
        if orchestrator is None:
            from seguro.core.orchestrator import orchestrator as default_orch
            orchestrator = default_orch
        self.orch = orchestrator
        self.replanner = replanner
        self.sem = asyncio.Semaphore(max_parallel)
        self.gui_lock = asyncio.Lock()
        self.max_replans = max_replans
        self.node_max_steps = node_max_steps
        self.escalation_llm = escalation_llm

    # ---- one node ---------------------------------------------------------------------------
    async def _attempt(self, run: WorkflowRun, node: Node, inputs: dict, feedback: str, llm=None) -> tuple:
        sink = {"expected": dict(node.expected_output), "values": {}}
        tok_out = current_node_outputs.set(sink)
        prompt, system_extra = _node_prompt(run, node, inputs, feedback)
        try:
            state = await self.orch.run_task(prompt, mode=run.mode, llm=llm, system_extra=system_extra,
                                             max_steps=self.node_max_steps, use_router=False)
        finally:
            current_node_outputs.reset(tok_out)
        auto = autopublish_from_verified(node, state, sink["values"])
        if auto:
            bus.emit("tool_call", tool="auto_output", args={}, ok=True, verified=True,
                     evidence=f"published from verified tool results: {sorted(auto)}", elapsed_ms=0)
        errors = []
        for k, v in sink["values"].items():
            try:
                run.bb.put(node.id, k, v, node.expected_output[k])
            except (OutputTypeError, ValueError) as e:
                errors.append(f"output {k}: {e}")
        return state, errors

    async def _run_node(self, run: WorkflowRun, node: Node):
        st = run.states[node.id]
        tok = current_context.set({"workflow_id": run.id, "node_id": node.id})
        try:
            st.status, st.started_at, st.error = "running", time.time(), None
            run.save()
            bus.emit("node_started", goal=node.goal, gui=node.gui, rung=node.preferred_rung, attempt=st.attempts + 1)

            if node.requires_confirmation:
                st.status = "waiting_confirmation"
                run.save()
                if not await broker.confirm(f"Workflow step '{node.id}': {node.goal}", tool=f"node:{node.id}"):
                    return self._fail(run, node, "Not approved by user.")
                st.status = "running"

            try:
                inputs = run.bb.resolve_obj(dict(node.inputs))
            except UnresolvedRef as e:
                return self._fail(run, node, f"Input not available: {e}")

            feedback = ""
            if self.escalation_llm is not None and run.mode == "hybrid":
                # Escalate after the first failed attempt: attempt 1 local, subsequent attempts cloud
                rem = max(0, node.max_attempts - st.attempts)
                if rem <= 1:
                    plans = [self.escalation_llm] if st.attempts >= 1 else [None]
                else:
                    plans = [None] + [self.escalation_llm] * (rem - 1)
            else:
                plans = [None] * max(0, node.max_attempts - st.attempts)
            for llm in plans:
                if killswitch.is_triggered():
                    st.status = "interrupted"
                    st.error = "Killswitch pressed"
                    run.save()
                    return
                st.attempts += 1
                st.escalated = st.escalated or llm is not None
                state, out_errors = await self._attempt(run, node, inputs, feedback, llm)
                st.tool_calls += state.step_count
                rungs = [determine_rung(s.tool) for s in state.history if s.tool not in ("node_output", "task_complete")]
                st.rung_used = rungs[-1] if rungs else st.rung_used
                if state.status == "aborted":
                    st.status, st.error = "interrupted", state.error_reason
                    run.save()
                    return
                if node.success_criteria:
                    # checks may do I/O (HTTP, Gmail lookup): keep the event loop free
                    ok, results = await asyncio.to_thread(run_checks, node.success_criteria, node.id, run.bb)
                else:
                    ok, results = False, []
                if run.graph.fallback:
                    agent_ok = state.status == "success"
                    ok = agent_ok and (ok or not node.success_criteria)
                    results.append({"type": "agent_verified_completion", "ok": agent_ok,
                                    "detail": state.final_evidence if agent_ok else
                                    (state.error_reason or "agent did not reach a verified completion")})
                st.check_results = results
                if ok:
                    st.status, st.ended_at = "verified", time.time()
                    run.save()
                    bus.emit("node_verified", checks=results, attempts=st.attempts, rung=st.rung_used,
                             escalated=st.escalated, elapsed_s=round(st.ended_at - st.started_at, 2))
                    return
                failed = [f"- {r['type']}: {r['detail']}" for r in results if not r["ok"]]
                feedback = "\n".join(failed + [f"- {e}" for e in out_errors] +
                                     ([f"- agent said: {state.error_reason}"] if state.error_reason else []))
                run.save()
            return self._fail(run, node, f"Checks failed after {st.attempts} attempt(s):\n{feedback}")
        finally:
            current_context.reset(tok)

    def _fail(self, run: WorkflowRun, node: Node, error: str):
        st = run.states[node.id]
        st.status, st.error, st.ended_at = "failed", error, time.time()
        run.save()
        bus.emit("node_failed", error=error[:2000], attempts=st.attempts, checks=st.check_results)

    async def _guarded(self, run: WorkflowRun, node: Node):
        if node.gui:
            async with self.gui_lock:   # one mouse and keyboard
                await self._run_node(run, node)
        else:
            async with self.sem:
                await self._run_node(run, node)

    # ---- whole graph -------------------------------------------------------------------------
    async def execute(self, run: WorkflowRun) -> WorkflowRun:
        bus.add_sink(run.id, f"{run.dir}\\events.jsonl")
        workflow_started_at.set(run.created_at)  # tasks created below inherit this context
        for st in run.states.values():   # resume: interrupted/running nodes run again
            if st.status in ("running", "interrupted", "waiting_confirmation"):
                st.status = "pending"
        run.status = "running"
        run.save()
        tasks: Dict[str, asyncio.Task] = {}
        try:
            while True:
                if killswitch.is_triggered():
                    for t in tasks.values():
                        await asyncio.wait([t], timeout=5)  # nodes check the switch themselves
                    run.status = "interrupted"
                    break
                self._skip_blocked(run)
                ready = [n for n in run.graph.nodes if run.states[n.id].status == "pending" and n.id not in tasks
                         and all(run.states[d].status == "verified" for d in n.depends_on)]
                for n in ready:
                    tasks[n.id] = asyncio.create_task(self._guarded(run, n))
                if not tasks:
                    break
                done, _ = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED)
                for nid in [k for k, t in tasks.items() if t in done]:
                    t = tasks.pop(nid)
                    if t.exception():
                        self._fail(run, run.graph.node(nid), f"Executor error: {t.exception()!r}")
                    if run.states[nid].status == "failed":
                        await self._maybe_replan(run, nid)
        finally:
            if run.status != "interrupted":
                run.status = "done" if all(s.status == "verified" for s in run.states.values()) else "failed"
            run.report = summarize(run)
            run.save()
            bus.emit("workflow_done", workflow_id=run.id, status=run.status, report=run.report,
                     replans=run.replans)
            bus.remove_sink(run.id)
        return run

    def _skip_blocked(self, run: WorkflowRun):
        changed = True
        while changed:
            changed = False
            for n in run.graph.nodes:
                st = run.states[n.id]
                if st.status == "pending" and any(run.states[d].status in ("failed", "skipped") for d in n.depends_on):
                    st.status, st.error = "skipped", "An upstream step failed."
                    changed = True

    async def _maybe_replan(self, run: WorkflowRun, failed_id: str):
        if self.replanner is None or run.replans >= self.max_replans:
            return
        new_graph = await self.replanner.revise(run, failed_id)
        if new_graph is None:
            return
        run.replans += 1
        old_ids = set(run.states)
        run.graph = new_graph
        for n in new_graph.nodes:
            if n.id not in run.states or run.states[n.id].status in ("failed", "skipped"):
                run.states[n.id] = NodeState()
        for nid in old_ids - {n.id for n in new_graph.nodes}:
            run.states.pop(nid, None)
        run.save()
        bus.emit("replanned", failed_node=failed_id, replans=run.replans,
                 nodes=[n.model_dump() for n in new_graph.nodes if run.states[n.id].status == "pending"])


PATH_HINTS = {
    (".xlsx", ".xls", ".csv"): ("xlsx", "excel", "sheet", "spreadsheet", "csv", "workbook"),
    (".docx", ".doc"): ("docx", "word", "doc", "report", "document"),
    (".pptx",): ("pptx", "slide", "deck", "presentation", "powerpoint"),
    (".png", ".jpg", ".jpeg"): ("png", "image", "chart", "screenshot", "picture", "img"),
}


def autopublish_from_verified(node: Node, state, values: dict) -> dict:
    """Fills missing outputs from VERIFIED tool results only (e.g. the path a verified
    excel_create_table_and_chart wrote). Never from the model's own claims. Returns what it filled."""
    filled = {}
    # Verified results of actions AND observations (news_search, files_list, web_fetch...): the data
    # came from the tool itself either way, never from the model's claims.
    verified = [s for s in state.history
                if (s.verifier_check or {}).get("verified") and (s.verifier_check or {}).get("kind") in ("act", "observe")
                and isinstance((s.result or {}).get("data"), dict)]
    observed = [s.result["data"] for s in verified if (s.verifier_check or {}).get("kind") == "observe"]
    paths, tables, urls = [], [], []
    for s in verified:
        d = s.result["data"]
        if d.get("path"):
            paths.append(d["path"])
        for m in d.get("moved") or []:
            paths.append(m["dst"])
        if d.get("table"):
            tables.append(d["table"])
        if d.get("url"):
            urls.append(d["url"])
    path_keys = [k for k, kind in node.expected_output.items() if kind == "path" and k not in values]
    for key in path_keys:
        k = key.lower()
        exts = next((e for e, words in PATH_HINTS.items() if any(w in k for w in words)), None)
        matches = [p for p in paths if exts and p.lower().endswith(exts)]
        if not matches and len(path_keys) == 1 and len(set(paths)) == 1:
            matches = paths
        if matches:
            values[key] = filled[key] = matches[-1]
    for key, kind in node.expected_output.items():
        if key in values:
            continue
        if kind == "table" and tables:
            values[key] = filled[key] = tables[-1]
        elif kind == "url" and urls:
            values[key] = filled[key] = urls[-1]
        elif kind == "json" and observed:
            # e.g. files_list entries or news articles: the latest verified observation's payload
            last = observed[-1]
            payload = next((last[k] for k in ("entries", "articles", "files", "objects") if k in last), last)
            values[key] = filled[key] = payload
        elif kind == "text" and observed:
            last = observed[-1]
            txt = last.get("answer") or last.get("text") or last.get("title")
            if txt:
                values[key] = filled[key] = txt
    return filled


def summarize(run: WorkflowRun) -> str:
    """One-paragraph honest report: what was verified, what failed and why, what was skipped."""
    v = [n.id for n in run.graph.nodes if run.states[n.id].status == "verified"]
    f = [(n.id, run.states[n.id].error) for n in run.graph.nodes if run.states[n.id].status == "failed"]
    s = [n.id for n in run.graph.nodes if run.states[n.id].status == "skipped"]
    i = [n.id for n in run.graph.nodes if run.states[n.id].status in ("interrupted", "pending", "running")]
    parts = [f"{len(v)}/{len(run.graph.nodes)} steps verified" + (f" ({', '.join(v)})" if v else "")]
    if f:
        parts.append("failed: " + "; ".join(f"{nid}: {(err or '').splitlines()[0][:160]}" for nid, err in f))
    if s:
        parts.append("skipped because an earlier step failed: " + ", ".join(s))
    if i:
        parts.append(f"not finished (resume with --resume {run.id}): " + ", ".join(i))
    if run.replans:
        parts.append(f"{run.replans} replan(s)")
    if run.plan_info.get("fallback"):
        parts.append("planner fell back to a single-step plan")
    return ". ".join(parts) + "."
