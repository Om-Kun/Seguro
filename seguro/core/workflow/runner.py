"""Entry points: run a request as a workflow, or resume a checkpointed one."""
import os
from typing import Optional

from seguro.core.events import bus
from seguro.core.workflow import checkpoint
from seguro.core.workflow.executor import Executor, WorkflowRun
from seguro.core.workflow.planner import Planner
from seguro.core.workflow.replanner import Replanner
from seguro.llm.client import LLMClient, load_config


def _settings():
    cfg = load_config()
    wf = cfg.get("workflow", {})
    root = os.path.expanduser(wf.get("root", "~/SeguroProjects/workflows"))
    return cfg, wf, root


async def _execute_and_clean(mode: str, run: WorkflowRun) -> WorkflowRun:
    """Servers started by the workflow and the automation browser end with it."""
    try:
        return await _executor(mode).execute(run)
    finally:
        from seguro.tools import process, webpage
        process.stop_all()
        webpage.close_browser()


def _executor(mode: str) -> Executor:
    _, wf, _ = _settings()
    esc = LLMClient.escalation() if mode == "hybrid" else None
    if mode == "hybrid" and esc is None:
        print("[Workflow] Hybrid mode: no escalation model configured/available; running locally only.")
    return Executor(replanner=Replanner(escalation_llm=esc), escalation_llm=esc,
                    max_parallel=int(wf.get("max_parallel", 3)), max_replans=int(wf.get("max_replans", 3)),
                    node_max_steps=int(wf.get("node_max_steps", 12)))


async def run_workflow(request: str, mode: str = "local_only", graph=None) -> WorkflowRun:
    _, _, root = _settings()
    info = {"fallback": False, "source": "given"}
    if graph is None:
        from seguro.core.workflow import templates
        hit = await templates.match(request)
        if hit:
            t, graph, params = hit
            info = {"fallback": False, "source": f"template:{t.name}", "params": params}
            print(f"[Workflow] Using saved template '{t.name}' with {params}")
        else:
            graph, info = await Planner().plan(request)
            info["source"] = "planner"
    if not graph.fallback:
        from seguro.core.workflow.planner import pin_requested_paths, derive_required_checks
        info["pinned_paths"] = pin_requested_paths(request, graph)
        info["derived_checks"] = derive_required_checks(request, graph)
    run = WorkflowRun(graph, request, mode, root=root, plan_info=info)
    bus.add_sink(run.id, os.path.join(run.dir, "events.jsonl"))
    bus.emit("workflow_started", workflow_id=run.id, request=request, mode=mode)
    bus.emit("plan_ready", workflow_id=run.id, graph=graph.model_dump(), plan_info=info)
    run.save()
    return await _execute_and_clean(mode, run)


async def resume_workflow(workflow_id: str, mode: Optional[str] = None) -> WorkflowRun:
    _, _, root = _settings()
    run = WorkflowRun.from_dict(checkpoint.load(workflow_id, root), root)
    if mode:
        run.mode = mode
    bus.add_sink(run.id, os.path.join(run.dir, "events.jsonl"))
    bus.emit("workflow_started", workflow_id=run.id, request=run.request, mode=run.mode, resumed=True)
    return await _execute_and_clean(run.mode, run)
