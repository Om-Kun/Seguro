"""Regression tests for the fake-success paths found in AUDIT_REPORT.md."""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from seguro.core.orchestrator import orchestrator, Orchestrator
from seguro.core.router import fast_router
from seguro.core.state import TaskState
from seguro.core.verifier import verifier
from seguro.tools.registry import registry, _parse_list
from seguro.tools.apps import apps_launch


@pytest.mark.asyncio
async def test_failed_script_is_not_ok_or_verified():
    res = await registry.dispatch({"tool": "run_powershell", "args": {"command": "exit 3"}})
    assert res["ok"] is False
    assert res["data"]["returncode"] == 3
    assert verifier.verify_action("run_powershell", {}, res)["verified"] is False


@pytest.mark.asyncio
async def test_aliased_args_and_stringified_lists_are_coerced():
    res = await registry.dispatch({"tool": "windows_focus", "args": {"title": "no-such-window-xyz"}})
    assert "unexpected keyword" not in str(res["error"])
    assert res["ok"] is False and res["data"]["status"] == "not_found"
    assert _parse_list("['ctrl', 's']") == ["ctrl", "s"]
    assert _parse_list("ctrl+s") == ["ctrl", "s"]


@pytest.mark.asyncio
async def test_unknown_args_report_valid_names():
    res = await registry.dispatch({"tool": "files_list", "args": {"bogus": 1, "root": "~/Desktop"}})
    assert res["ok"] is False and "Valid arguments" in res["error"]


def test_input_actions_are_never_verified():
    for tool in ("type_text", "hotkey", "click_xy", "uia_click"):
        v = verifier.verify_action(tool, {}, {"ok": True, "data": {"ok": True}})
        assert v["verified"] is False


def test_vision_tools_fail_honestly():
    from seguro.tools.vision import ground_and_click, look_at_screen
    assert ground_and_click("the OK button")["ok"] is False
    r = look_at_screen("what is open?")
    assert "image_b64" not in r
    assert (r["ok"] and r.get("answer")) or (not r["ok"] and r.get("error"))  # real answer or honest error


def test_apps_launch_never_shells_free_text():
    r = apps_launch("the file foo.html from my desktop and tell me things")
    assert r["status"] == "error"


def test_router_rejects_compound_prompts():
    assert fast_router.match("Search the web for 'x' in Brave and tell me the title") is None
    assert fast_router.match("Open the file probe.html from my Desktop in Brave") is None
    assert fast_router.match("Open Notepad, type hi, and save it") is None
    assert fast_router.match("open calculator")["tool"] == "apps_launch"


def _state_with(*steps):
    st = TaskState(request="t", mode="local_only")
    for tool, check in steps:
        st.record_step(tool, {}, {"ok": check.get("verified", False)}, check)
    return st


def test_unrelated_verified_step_does_not_mask_a_failure():
    st = _state_with(
        ("run_python", {"verified": False, "kind": "act", "evidence": "rc=1"}),
        ("settings_set_theme", {"verified": True, "kind": "act", "evidence": "ok"}),
    )
    v = verifier.verify_action("task_complete", {"evidence": "Benchmark written."}, {"ok": True}, state=st)
    assert v["verified"] is False


def test_folder_path_is_not_file_evidence():
    st = _state_with(("run_python", {"verified": False, "kind": "act", "evidence": "rc=1"}))
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    v = verifier.verify_action("task_complete", {"evidence": f"Saved in {desktop}"}, {"ok": True}, state=st)
    assert v["verified"] is False


def test_completion_needs_a_verified_step():
    st = _state_with(("type_text", {"verified": False, "kind": "act", "warning": "unverifiable_input_action"}))
    v = verifier.verify_action("task_complete", {"evidence": "Typed and saved the file."}, {"ok": True}, state=st)
    assert v["verified"] is False


def test_text_emitted_tool_call_is_parsed():
    call = Orchestrator._extract_text_tool_call(
        'Sure. {"name": "files_write", "parameters": {"path": "~/Desktop/a.txt", "content": "x"}}')
    assert call and call["tool"] == "files_write" and call["args"]["path"] == "~/Desktop/a.txt"
    assert Orchestrator._extract_text_tool_call('{"name": "not_a_tool", "parameters": {}}') is None


@pytest.mark.asyncio
async def test_local_only_blocks_delegation():
    st = TaskState(request="t", mode="local_only")
    res = await registry.dispatch({"tool": "delegate_to_coding_agent", "args": {"task": "x"}}, state=st)
    assert res["ok"] is False and "LOCAL-ONLY" in res["error"]
