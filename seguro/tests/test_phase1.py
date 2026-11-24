import os
import sys
import time
import json
import asyncio
import pytest

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure seguro parent folder is in python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from seguro.safety.killswitch import killswitch
from seguro.safety.audit_log import audit_logger
from seguro.safety.gate import safety_gate
from seguro.tools.registry import registry
from seguro.llm.client import LLMClient

@pytest.mark.asyncio
async def test_killswitch_cancels_immediately():
    """Acceptance test: F12 / killswitch cancels execution loop within 300ms."""
    killswitch.reset()
    assert not killswitch.is_triggered()
    
    t0 = time.time()
    
    # Simulate background abort trigger after 50ms
    def delayed_trigger():
        time.sleep(0.05)
        killswitch.trigger()
        
    asyncio.get_event_loop().run_in_executor(None, delayed_trigger)
    
    cancelled = False
    for _ in range(300): # 30 second simulated loop
        if killswitch.is_triggered():
            cancelled = True
            break
        await asyncio.sleep(0.01)
        
    elapsed = time.time() - t0
    assert cancelled, "Killswitch failed to cancel loop"
    assert elapsed < 0.30, f"Killswitch response was {elapsed}s, must be < 300ms"
    print(f"✅ Killswitch passed: cancelled in {elapsed*1000:.1f}ms (< 300ms)")
    killswitch.reset()

@pytest.mark.asyncio
async def test_audit_logger_records_real_jsonl():
    """Acceptance test: Every action is logged to real JSONL file on disk."""
    test_id = f"test_{int(time.time()*1000)}"
    audit_logger.log_action(
        tool_name="apps_launch",
        args={"name": "notepad"},
        result={"ok": True, "data": "launched"},
        elapsed_ms=45.2,
        task_id=test_id
    )
    
    log_path = audit_logger._get_log_path()
    assert os.path.exists(log_path), "Audit log file was not created"
    
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    found = False
    for line in reversed(lines):
        record = json.loads(line)
        if record.get("task_id") == test_id:
            found = True
            assert record["tool"] == "apps_launch"
            assert record["elapsed_ms"] == 45.2
            break
            
    assert found, "Logged record was not found in audit log file"
    print(f"✅ Audit logger passed: verified real entry in {log_path}")

@pytest.mark.asyncio
async def test_safety_gate_blocks_destructive_commands():
    """Acceptance test: Destructive commands and system paths are rejected."""
    # Test destructive powershell command
    ok, reason = safety_gate.check_command("Remove-Item -Recurse C:\\Users\\test")
    assert not ok, "Safety gate failed to block Remove-Item -Recurse"
    
    # Test protected system directory
    ok, reason = safety_gate.check_path("C:\\Windows\\System32\\calc.exe")
    assert not ok, "Safety gate failed to protect C:\\Windows"
    
    # Test allowed user path
    desktop = os.path.join(os.path.expanduser("~"), "Desktop", "test.txt")
    ok, reason = safety_gate.check_path(desktop)
    assert ok, f"Safety gate falsely rejected allowed Desktop path: {reason}"
    print("✅ Safety gate passed: destructive commands and system paths strictly blocked")

@pytest.mark.asyncio
async def test_tool_registry_safe_dispatch():
    """Acceptance test: Tools return {ok, data, error, elapsed_ms} and never raise."""
    @registry.register(
        name="dummy_calc",
        description="Adds two numbers",
        parameters={
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"]
        }
    )
    def dummy_calc(a: float, b: float):
        return a + b

    # Test success
    res = await registry.dispatch({"tool": "dummy_calc", "args": {"a": 10, "b": 32}})
    assert res["ok"] is True
    assert res["data"] == 42
    assert "elapsed_ms" in res
    
    # Test missing tool (must not raise)
    res_missing = await registry.dispatch({"tool": "nonexistent_tool", "args": {}})
    assert res_missing["ok"] is False
    assert "not found" in res_missing["error"]
    
    print("✅ Tool registry passed: safe structured dispatch confirmed")

if __name__ == "__main__":
    asyncio.run(test_killswitch_cancels_immediately())
    asyncio.run(test_audit_logger_records_real_jsonl())
    asyncio.run(test_safety_gate_blocks_destructive_commands())
    asyncio.run(test_tool_registry_safe_dispatch())
    print("\n🎉 ALL PHASE 1 ACCEPTANCE TESTS PASSED!")
