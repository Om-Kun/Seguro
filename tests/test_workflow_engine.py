"""Executor / planner / replanner / gates tests with a scripted fake orchestrator (no LLM, no GUI)."""
import asyncio
import json
import os
import sys
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from seguro.core.state import TaskState
from seguro.core.events import bus, read_events
from seguro.core.workflow.models import TaskGraph
from seguro.core.workflow.executor import Executor, WorkflowRun
from seguro.core.workflow.node_context import current_node_outputs
from seguro.core.workflow.planner import Planner, is_complex
from seguro.core.workflow.replanner import merge
from seguro.core.workflow.gates import classify, ConfirmationBroker
from seguro.core.workflow import checkpoint
from seguro.safety.killswitch import killswitch


class FakeOrch:
    """Behaviour per node id (parsed from the prompt 'Your step: <id>: ...')."""
    def __init__(self, behaviours, tmp):
        self.behaviours, self.tmp, self.calls, self.intervals = behaviours, tmp, [], {}

    async def run_task(self, prompt, mode=None, llm=None, system_extra=None, max_steps=None, use_router=True):
        nid = prompt.split("Your step: ")[1].split(":")[0]
        self.calls.append(nid)
        b = self.behaviours.get(nid, {})
        t0 = time.time()
        await asyncio.sleep(b.get("sleep", 0.01))
        if b.get("kill"):
            killswitch.trigger()
        st = TaskState(request=prompt, mode=mode)
        sink = current_node_outputs.get()
        n_calls = self.calls.count(nid)
        if b.get("publish", True) and n_calls > b.get("fail_times", 0):
            p = os.path.join(self.tmp, f"{nid}.txt")
            open(p, "w").write(nid)
            sink["values"]["file_path"] = p
        st.status = "aborted" if killswitch.is_triggered() else b.get("agent_status", "success")
        self.intervals.setdefault(nid, []).append((t0, time.time()))
        return st


def graph(spec):
    nodes = []
    for nid, deps, gui in spec:
        nodes.append({"id": nid, "goal": f"{nid}: do the {nid} step", "depends_on": deps, "gui": gui,
                      "expected_output": {"file_path": "path"}, "max_attempts": 2,
                      "success_criteria": [{"type": "file_exists", "path": "{{" + nid + ".file_path}}"}]})
    return TaskGraph.model_validate({"goal": "test", "nodes": nodes})


def make_run(g, tmp_path, mode="local_only"):
    return WorkflowRun(g, "req", mode, root=str(tmp_path / "wf"))


@pytest.fixture(autouse=True)
def _reset_kill():
    killswitch.reset()
    yield
    killswitch.reset()


@pytest.mark.asyncio
async def test_non_gui_nodes_run_in_parallel_and_gui_nodes_never_overlap(tmp_path):
    g = graph([("a", [], False), ("b", [], False), ("c", [], False), ("g1", [], True), ("g2", [], True)])
    orch = FakeOrch({k: {"sleep": 0.3} for k in "abc"} | {"g1": {"sleep": 0.2}, "g2": {"sleep": 0.2}}, str(tmp_path))
    run = await Executor(orchestrator=orch, max_parallel=3).execute(make_run(g, tmp_path))
    assert run.status == "done", run.report
    a, b = orch.intervals["a"][0], orch.intervals["b"][0]
    assert a[0] < b[1] and b[0] < a[1], "non-GUI nodes should overlap"
    g1, g2 = orch.intervals["g1"][0], orch.intervals["g2"][0]
    assert g1[1] <= g2[0] or g2[1] <= g1[0], "GUI nodes must not overlap"


@pytest.mark.asyncio
async def test_agent_claiming_success_without_outputs_is_not_verified(tmp_path):
    g = graph([("a", [], False), ("b", ["a"], False)])
    orch = FakeOrch({"a": {"publish": False, "agent_status": "success"}}, str(tmp_path))
    run = await Executor(orchestrator=orch).execute(make_run(g, tmp_path))
    assert run.states["a"].status == "failed" and run.states["a"].attempts == 2
    assert run.states["b"].status == "skipped" and run.status == "failed"
    assert "0/2 steps verified" in run.report and "skipped" in run.report


@pytest.mark.asyncio
async def test_retry_feedback_then_verified_and_events_logged(tmp_path):
    g = graph([("a", [], False)])
    orch = FakeOrch({"a": {"fail_times": 1}}, str(tmp_path))
    run = await Executor(orchestrator=orch).execute(make_run(g, tmp_path))
    assert run.states["a"].status == "verified" and run.states["a"].attempts == 2
    types = [e["type"] for e in read_events(os.path.join(run.dir, "events.jsonl"))]
    assert types.count("node_started") == 1 and "node_verified" in types and types[-1] == "workflow_done"


@pytest.mark.asyncio
async def test_kill_mid_run_then_resume_skips_verified_nodes(tmp_path):
    g = graph([("a", [], False), ("b", ["a"], False), ("c", ["b"], False)])
    orch = FakeOrch({"b": {"kill": True}}, str(tmp_path))
    run = await Executor(orchestrator=orch).execute(make_run(g, tmp_path))
    assert run.status == "interrupted"
    assert run.states["a"].status == "verified" and run.states["b"].status == "interrupted"

    killswitch.reset()
    saved = checkpoint.load(run.id, str(tmp_path / "wf"))
    resumed = WorkflowRun.from_dict(saved, str(tmp_path / "wf"))
    orch2 = FakeOrch({}, str(tmp_path))
    done = await Executor(orchestrator=orch2).execute(resumed)
    assert done.status == "done"
    assert orch2.calls == ["b", "c"], "verified node 'a' must not run again"


class AlwaysFailingReplanner:
    def __init__(self):
        self.n = 0

    async def revise(self, run, failed_id):
        self.n += 1
        new = graph([(f"r{self.n}", [], False)]).nodes
        return merge(run.graph, [failed_id] + run.graph.descendants(failed_id), new)


@pytest.mark.asyncio
async def test_replans_are_capped_and_report_is_honest(tmp_path):
    g = graph([("a", [], False)])
    behaviours = {"a": {"publish": False}} | {f"r{i}": {"publish": False} for i in range(1, 10)}
    rp = AlwaysFailingReplanner()
    run = await Executor(orchestrator=FakeOrch(behaviours, str(tmp_path)), replanner=rp, max_replans=3).execute(
        make_run(g, tmp_path))
    assert run.replans == 3 and rp.n == 3 and run.status == "failed"
    assert "3 replan(s)" in run.report


@pytest.mark.asyncio
async def test_fallback_node_needs_agent_verified_completion(tmp_path):
    from seguro.core.workflow.planner import fallback_graph
    g = fallback_graph("whole_task: do everything")
    orch = FakeOrch({"whole_task": {"publish": False, "agent_status": "failed"}}, str(tmp_path))
    run = await Executor(orchestrator=orch).execute(make_run(g, tmp_path))
    assert run.states["whole_task"].status == "failed"


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    async def complete_json(self, messages, schema, name="x", **kw):
        self.seen.append(messages)
        return self.replies.pop(0)


@pytest.mark.asyncio
async def test_planner_repairs_once_then_falls_back():
    good = json.dumps({"goal": "g", "nodes": [{"id": "a", "goal": "make a file", "expected_output": {"f": "path"}}]})
    bad = json.dumps({"goal": "g", "nodes": [{"id": "a", "goal": "make a file", "depends_on": ["Excel"],
                                              "expected_output": {"f": "path"}}]})
    g, info = await Planner(FakeLLM([bad, good])).plan("x")
    assert not info["fallback"] and info["attempts"] == 2 and g.nodes[0].id == "a"
    g, info = await Planner(FakeLLM([bad, "not json"])).plan("x")
    assert info["fallback"] and g.fallback and len(g.nodes) == 1


def test_complexity_gate():
    assert not is_complex("open calculator")
    assert not is_complex("Create a file notes.txt on the Desktop with hello")
    assert is_complex("Find 5 AI news articles, put them in an Excel sheet, make a chart, write a Word report "
                      "and create a Gmail draft")


@pytest.mark.asyncio
async def test_gates_classify_and_deny_by_default(tmp_path):
    assert classify("settings_set_theme", {"mode": "dark"})
    assert classify("delegate_to_coding_agent", {"task": "x"})
    assert classify("run_powershell", {"command": "Remove-Item C:\\x.txt"})
    assert classify("browser_page_click", {"text": "Send"})
    assert classify("files_write", {"path": __file__}) and classify("files_write", {"path": str(tmp_path / "new.txt")}) is None
    assert classify("files_list", {"root": "~"}) is None

    b = ConfirmationBroker()
    async def slow(reason, details):
        await asyncio.sleep(5)
        return True
    b.configure(responder=slow, timeout_s=0.1)
    assert await b.confirm("x", tool="settings_set_theme") is False          # timeout = deny
    b.configure(auto_approve={"settings_set_theme"})
    assert await b.confirm("x", tool="settings_set_theme") is True


@pytest.mark.asyncio
async def test_dispatch_blocks_unapproved_setting_change():
    from seguro.tools.registry import registry
    from seguro.core.workflow.gates import broker
    import seguro.skills.library.settings_theme  # noqa: F401
    async def deny(reason, details):
        return False
    broker.configure(responder=deny, auto_approve=set())
    res = await registry.dispatch({"tool": "settings_set_theme", "args": {"mode": "light"}})
    assert res["ok"] is False and "Not approved" in res["error"]
