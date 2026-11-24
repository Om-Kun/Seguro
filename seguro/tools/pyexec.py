import os
import re
import sys
import tempfile
import subprocess
from seguro.tools.registry import registry
from seguro.safety.gate import safety_gate

# Prepended to every script: lets open('~/Desktop/x.txt') work like the user meant. Output is
# still checked by the verifier, this only fixes a path spelling small models get wrong.
PRELUDE = (
    "import builtins as _b, os as _os\n"
    "_seguro_open = _b.open\n"
    "def _seguro_expand_open(f, *a, **k):\n"
    "    return _seguro_open(_os.path.expanduser(f) if isinstance(f, str) else f, *a, **k)\n"
    "_b.open = _seguro_expand_open\n"
    "del _b\n"
)

_PY_C = re.compile(r'^\s*python[3]?(?:\.exe)?\s+-c\s+(["\'])(.*)\1\s*(?:>\s*(\S+))?\s*$', re.DOTALL)


def normalize_code(code: str) -> tuple:
    """Fixes common small-model syntax slips without changing intent. Returns (code, notes)."""
    notes = []
    m = _PY_C.match(code)
    if m:  # python -c "..." [> file] written as if it were Python
        code = m.group(2).replace('\\"', '"')
        notes.append("unwrapped python -c")
        if m.group(3):
            target = m.group(3)
            code = ("import sys as _sys, os as _os2\n"
                    f"_sys.stdout = open(_os2.path.expanduser({target!r}), 'w', encoding='utf-8')\n" + code)
            notes.append(f"redirected stdout to {target}")
    try:
        compile(code, "<seguro>", "exec")
    except SyntaxError:
        alt = code.replace("; ", "\n")
        try:
            compile(alt, "<seguro>", "exec")
            code = alt
            notes.append("split '; ' statements onto lines")
        except SyntaxError:
            pass  # leave it; the real error is reported back
    return code, notes


@registry.register(
    name="run_python",
    description="Executes a Python script in an isolated subprocess with timeout and captured output.",
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source code to execute"},
            "timeout_s": {"type": "number", "description": "Execution timeout in seconds (default: 30)"}
        },
        "required": ["code"]
    }
)
def run_python(code: str, timeout_s: float = 30.0) -> dict:
    try:
        timeout_s = float(timeout_s)
    except Exception:
        timeout_s = 30.0
    ok, reason = safety_gate.check_command(code)
    if not ok:
        return {"returncode": -1, "stdout": "", "stderr": f"Safety gate denied Python code: {reason}"}
    code, notes = normalize_code(code)

    # Write to temporary script file
    temp_dir = tempfile.gettempdir()
    script_path = os.path.join(temp_dir, f"seguro_task_{os.getpid()}_{hash(code) & 0xFFFFFFFF}.py")
    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(PRELUDE + code)

        # Execute using current python interpreter in isolated (-I) mode
        res = subprocess.run(
            [sys.executable, "-I", script_path],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=os.path.expanduser("~")
        )
        out = {
            "returncode": res.returncode,
            "stdout": res.stdout.strip(),
            "stderr": res.stderr.strip()
        }
        if notes:
            out["normalized"] = notes
        return out
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": f"Python execution timed out after {timeout_s}s."}
    except Exception as e:
        return {"returncode": -1, "stdout": "", "stderr": str(e)}
    finally:
        if os.path.exists(script_path):
            try:
                os.remove(script_path)
            except Exception:
                pass
