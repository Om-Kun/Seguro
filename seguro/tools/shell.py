import subprocess
from seguro.tools.registry import registry
from seguro.safety.gate import safety_gate

@registry.register(
    name="run_powershell",
    description="Executes a safe PowerShell command and returns stdout, stderr, and exit code.",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "PowerShell command line string to execute"},
            "timeout_s": {"type": "number", "description": "Execution timeout in seconds (default: 30)"}
        },
        "required": ["command"]
    }
)
def run_powershell(command: str, timeout_s: float = 30.0) -> dict:
    try:
        timeout_s = float(timeout_s)
    except Exception:
        timeout_s = 30.0
    ok, reason = safety_gate.check_command(command)
    if not ok:
        return {"returncode": -1, "stdout": "", "stderr": f"Safety gate denied command: {reason}"}

    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=timeout_s
        )
        return {
            "returncode": res.returncode,
            "stdout": res.stdout.strip(),
            "stderr": res.stderr.strip()
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": f"Command timed out after {timeout_s}s."}
    except Exception as e:
        return {"returncode": -1, "stdout": "", "stderr": str(e)}
