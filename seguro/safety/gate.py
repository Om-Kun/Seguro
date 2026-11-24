import os
import re

DESTRUCTIVE_PATTERNS = [
    r"\bformat\s+[a-zA-Z]:",
    r"\bFormat-Volume\b",
    r"\brmdir\b\s+/[sS]",
    r"\bdel\b\s+/[fFqQsS]",
    r"\bRemove-Item\b.*-Recurse",
    r"\bshutil\.rmtree\b",
    r"\bshutdown\s+/[sSrR]",
    r"\btaskkill\b\s+/f",
    r"\breg\s+delete\b",
    r"\bdrop\s+database\b",
    r"\btruncate\s+table\b",
]

DEFAULT_ALLOWED_ROOTS = [
    os.path.join(os.path.expanduser("~"), "Desktop"),
    os.path.join(os.path.expanduser("~"), "Documents"),
    os.path.join(os.path.expanduser("~"), "Downloads"),
    os.path.join(os.path.expanduser("~"), "SeguroProjects"),
]

PROTECTED_SYSTEM_PATHS = [
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\System32",
]

class SafetyGate:
    def __init__(self, allowed_roots: list = None):
        self.allowed_roots = allowed_roots or DEFAULT_ALLOWED_ROOTS

    def check_command(self, cmd_or_code: str) -> tuple[bool, str]:
        """Checks if a shell command or script contains destructive operations."""
        for pattern in DESTRUCTIVE_PATTERNS:
            if re.search(pattern, cmd_or_code, re.IGNORECASE):
                return False, f"Potentially destructive pattern matched: '{pattern}'"
        return True, "Command passed safety gate"

    def check_path(self, target_path: str) -> tuple[bool, str]:
        """Ensures file access stays strictly inside user workspace allowlists."""
        # Resolve exactly as the file tools do (bare names land on the Desktop).
        from seguro.tools.files import normalize_user_path
        abs_target = normalize_user_path(target_path) if target_path else ""
        if not abs_target:
            return False, "Empty path."
        
        # Check system protection
        for sys_path in PROTECTED_SYSTEM_PATHS:
            if abs_target.lower().startswith(sys_path.lower()):
                return False, f"Access to system path '{sys_path}' is strictly blocked."

        # Check allowed roots
        t = abs_target.lower().rstrip("\\/")
        is_allowed = any(t == r.lower().rstrip("\\/") or t.startswith(r.lower().rstrip("\\/") + os.sep)
                         for r in self.allowed_roots)
        if not is_allowed:
            return False, f"Target path '{target_path}' is outside permitted directories."

        return True, "Path approved"

    async def gate(self, call_spec: dict, state=None) -> tuple[bool, str]:
        """Checked by ToolRegistry.dispatch before every tool call. Returns (allowed, reason)."""
        tool_name = call_spec.get("tool", "")
        args = call_spec.get("args", {}) or {}

        def deny(reason: str):
            print(f"🛑 [SAFETY GATE DENIED] {tool_name}: {reason}")
            return False, reason

        if tool_name in ["run_powershell", "shell"]:
            ok, reason = self.check_command(str(args.get("command", "")))
            if not ok:
                return deny(reason)

        if tool_name in ["run_python", "pyexec"]:
            ok, reason = self.check_command(str(args.get("code", "")))
            if not ok:
                return deny(reason)

        if tool_name in ["files_write", "files_delete", "files_mkdir"]:
            ok, reason = self.check_path(str(args.get("path") or ""))
            if not ok:
                return deny(reason)

        if tool_name == "files_move":
            srcs = args.get("src")
            for p in (srcs if isinstance(srcs, list) else [srcs]) + [args.get("dst")]:
                ok, reason = self.check_path(str(p or ""))
                if not ok:
                    return deny(reason)

        # Delegation and cloud computer-use send the task off this PC.
        mode = getattr(state, "mode", None)
        if mode == "local_only" and (tool_name.startswith("delegate") or "cloud" in tool_name):
            return deny("LOCAL-ONLY mode: tools that send data off this PC are disabled.")

        # Risky but legitimate actions (settings, overwrite, delete, send/post/buy) need a human yes.
        from seguro.core.workflow.gates import classify, broker
        reason = classify(tool_name, args)
        if reason and not await broker.confirm(reason, tool=tool_name, details=args):
            return deny(f"Not approved by user: {reason}")

        return True, "allowed"

# Global default safety gate instance
safety_gate = SafetyGate()
