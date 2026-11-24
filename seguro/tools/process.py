"""Background processes (e.g. a dev server) with port wait and cleanup."""
import os
import shlex
import socket
import subprocess
import sys
import time

from seguro.tools.registry import registry
from seguro.safety.gate import safety_gate

_PROCS = {}  # name -> Popen


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


@registry.register(
    name="process_start",
    description=("Starts a long-running program in the background (e.g. 'python app.py') and optionally waits "
                 "until a local port accepts connections. Use process_stop when finished."),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short name to refer to it later"},
            "command": {"type": "string", "description": "Command line, e.g. 'python app.py'"},
            "cwd": {"type": "string", "description": "Working folder"},
            "wait_port": {"type": "integer", "description": "Local port to wait for (optional)"},
            "timeout_s": {"type": "number", "description": "How long to wait for the port (default 20)"}
        },
        "required": ["name", "command"]
    }
)
def process_start(name: str, command: str, cwd: str = "~/SeguroProjects", wait_port: int = 0,
                  timeout_s: float = 20) -> dict:
    from seguro.tools.files import normalize_user_path
    ok, reason = safety_gate.check_command(command)
    if not ok:
        return {"ok": False, "error": reason}
    real_cwd = normalize_user_path(cwd)
    ok, reason = safety_gate.check_path(real_cwd)
    if not ok or not os.path.isdir(real_cwd):
        return {"ok": False, "error": reason if not ok else f"Folder '{real_cwd}' does not exist."}
    if wait_port and _port_open(int(wait_port)):
        return {"ok": False, "error": f"Port {wait_port} is already in use; pick another port."}
    argv = shlex.split(command, posix=False)
    if argv and argv[0].lower() in ("python", "python.exe", "py"):
        argv[0] = sys.executable
    process_stop(name)
    log_path = os.path.join(real_cwd, f".seguro_{name}.log")
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(argv, cwd=real_cwd, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _PROCS[name] = proc
    if wait_port:
        deadline = time.time() + float(timeout_s)
        while time.time() < deadline:
            if proc.poll() is not None:
                log.flush()
                tail = open(log_path, encoding="utf-8", errors="replace").read()[-800:]
                return {"ok": False, "error": f"Process exited with code {proc.returncode}. Output: {tail}"}
            if _port_open(int(wait_port)):
                return {"ok": True, "name": name, "pid": proc.pid, "port": int(wait_port), "log": log_path}
            time.sleep(0.3)
        return {"ok": False, "error": f"Port {wait_port} did not open within {timeout_s}s.", "pid": proc.pid,
                "log": log_path}
    time.sleep(1.0)
    if proc.poll() is not None:
        return {"ok": False, "error": f"Process exited immediately with code {proc.returncode}.", "log": log_path}
    return {"ok": True, "name": name, "pid": proc.pid, "log": log_path}


@registry.register(
    name="process_stop",
    description="Stops a background program started with process_start.",
    parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
)
def process_stop(name: str) -> dict:
    proc = _PROCS.pop(name, None)
    if proc is None:
        return {"ok": False, "error": f"No running process named '{name}'."}
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return {"ok": True, "name": name, "exit_code": proc.returncode}


def stop_all():
    for name in list(_PROCS):
        process_stop(name)
