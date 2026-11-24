import os
import re
import sys
import time
import shutil
import subprocess
from typing import Optional, Dict, Any, List
from seguro.tools.registry import registry
from seguro.safety.gate import safety_gate

# CLI coding agents and their standard execution invocations
AGENT_TEMPLATES = {
    "claude": ["claude", "-p", "{task}", "--output-format", "json"],
    "codex":  ["codex", "exec", "{task}", "--skip-git-repo-check"],
    "gemini": ["gemini", "-p", "{task}"],
}

def _find_available_agent(preference_order: List[str]) -> Optional[str]:
    """Finds first available agent binary on system PATH."""
    for ag in preference_order:
        if shutil.which(ag) or shutil.which(f"{ag}.exe"):
            return ag
    return None

def _slugify(text: str) -> str:
    clean = re.sub(r'[^a-zA-Z0-9_\-]+', '-', text.lower().strip())
    return clean[:30].strip('-') or "project"

@registry.register(
    name="delegate_to_coding_agent",
    description="Delegates complex multi-file coding projects or deep debugging tasks to an autonomous CLI coding agent (Claude Code, Codex, or Gemini) in ~/SeguroProjects/.",
    parameters={
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Clear specification of the coding task or app to build"},
            "agent": {"type": "string", "description": "Optional specific agent preference ('claude', 'codex', 'gemini')"},
            "slug": {"type": "string", "description": "Optional short project slug for folder naming"},
            "timeout_s": {"type": "number", "description": "Execution timeout in seconds (default: 360)"}
        },
        "required": ["task"]
    }
)
def delegate_to_coding_agent(
    task: str,
    agent: Optional[str] = None,
    slug: Optional[str] = None,
    timeout_s: float = 360.0
) -> dict:
    """
    Executes a coding agent in a fresh, isolated workspace under ~/SeguroProjects/<slug>.
    """
    # 1. Determine agent
    pref_order = [agent] if agent else ["claude", "codex", "gemini"]
    chosen_agent = _find_available_agent(pref_order)
    
    if not chosen_agent:
        return {
            "ok": False,
            "error": f"No supported coding agent found on PATH (checked: {pref_order}). Please install claude or codex.",
            "workdir": None,
            "files": []
        }

    # 2. Setup project workdir
    proj_slug = slug or _slugify(task)
    root = os.path.expanduser(r"~\SeguroProjects")
    workdir = os.path.join(root, f"{proj_slug}_{int(time.time())}")
    os.makedirs(workdir, exist_ok=True)

    # 3. Format command
    template = AGENT_TEMPLATES.get(chosen_agent, [chosen_agent, "{task}"])
    cmd = [arg.replace("{task}", task) for arg in template]

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            encoding="utf-8",
            errors="replace"
        )
        elapsed_s = time.time() - t0
        
        # Scan created files
        created_files = []
        for r, _, fnames in os.walk(workdir):
            for fn in fnames:
                created_files.append(os.path.relpath(os.path.join(r, fn), workdir))

        return {
            "ok": proc.returncode == 0 or len(created_files) > 0,
            "agent": chosen_agent,
            "workdir": workdir,
            "files": created_files,
            "returncode": proc.returncode,
            "stdout": proc.stdout[:1000],
            "stderr": proc.stderr[:500],
            "elapsed_s": round(elapsed_s, 2)
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "agent": chosen_agent,
            "error": f"Coding agent timed out after {timeout_s}s",
            "workdir": workdir,
            "files": []
        }
    except Exception as e:
        return {
            "ok": False,
            "agent": chosen_agent,
            "error": str(e),
            "workdir": workdir,
            "files": []
        }
