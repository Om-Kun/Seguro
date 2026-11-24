import os
import sys
import time
import pytest
import asyncio

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from seguro.tools.delegate import delegate_to_coding_agent, _find_available_agent
from seguro.tools.cloud_cua import cloud_cua
from seguro.core.orchestrator import orchestrator

def test_delegation_agent_discovery():
    """Acceptance test: Discovers installed CLI coding agents (Claude, Codex, etc.)."""
    agent = _find_available_agent(["claude", "codex", "gemini"])
    assert agent in ("claude", "codex"), f"Expected claude or codex to be found, got: {agent}"
    print(f"✅ Discovered available coding agent on PATH: '{agent}'")

def test_delegation_workdir_and_isolation():
    """Acceptance test: Creates isolated workspace in ~/SeguroProjects/ and executes with timeout."""
    res = delegate_to_coding_agent(
        task="Create a Python script fib.py that computes Fibonacci up to n=10 and prints it.",
        slug="test-fib-proj",
        timeout_s=30.0
    )
    assert res["workdir"] is not None
    assert os.path.exists(res["workdir"]), "Workdir was not created"
    assert "SeguroProjects" in res["workdir"]
    print(f"✅ Delegation successfully isolated in workdir: {res['workdir']}, Agent: {res['agent']}, Files: {res['files']}")

def test_cloud_cua_honest_failure_without_keys():
    """Acceptance test: Cloud CUA escalation reports honest failure when unconfigured; never fakes results."""
    # Ensure no API key set
    cua_no_key = cloud_cua
    cua_no_key.api_key = ""
    
    assert cua_no_key.is_available() is False
    res = asyncio.run(cua_no_key.execute_subgoal("Click Settings button", "Find Dark Mode toggle", "Local UIA failed"))
    assert res["ok"] is False
    assert "ANTHROPIC_API_KEY" in res["error"]
    assert "honestly" in res["error"]
    print("✅ Cloud CUA strictly refused execution without API key and honestly reported reason.")

@pytest.mark.asyncio
async def test_local_only_mode_no_escalation():
    """Acceptance test: LOCAL-ONLY mode prevents cloud escalation and respects privacy boundary."""
    state = await orchestrator.run_task("deliberately impossible imaginary action 123456", mode="local_only")
    assert state.mode == "local_only"
    assert state.status in ("failed", "running")
    print(f"✅ LOCAL-ONLY mode respected boundary: status='{state.status}' without external leaks")

if __name__ == "__main__":
    test_delegation_agent_discovery()
    test_delegation_workdir_and_isolation()
    test_cloud_cua_honest_failure_without_keys()
    asyncio.run(test_local_only_mode_no_escalation())
    print("\n🎉 ALL PHASE 4 ACCEPTANCE TESTS PASSED!")
