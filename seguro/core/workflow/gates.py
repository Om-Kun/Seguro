"""
Human confirmation for risky actions. Enforced at tool dispatch (seguro/safety/gate.py calls
classify + broker), so an unplanned risky call still stops, not only nodes flagged by the planner.
Policy: anything not explicitly approved is denied; a timeout counts as deny.
"""
import asyncio
import os
import re
import sys
from typing import Awaitable, Callable, Optional, Set

from seguro.core.events import bus

# Spending money always needs a live human yes: config, evals or tests can never pre-approve these.
NEVER_AUTO_APPROVE = {"zomato_place_order"}

OFFICE_OUTPUT_EXT = {"excel_create_table_and_chart": ".xlsx", "skill_report_generator": ".xlsx",
                     "word_create_document": ".docx", "pptx_create": ".pptx", "chart_png": ".png"}

RISKY_WORDS = re.compile(r"\b(send|post|publish|tweet|buy|purchase|order|checkout|pay|delete|remove|unsubscribe)\b",
                         re.IGNORECASE)
DELETE_CODE = re.compile(r"\b(Remove-Item|del|erase|rm|rmdir|os\.remove|os\.unlink|unlink\(|shutil\.rmtree|"
                         r"send2trash|Clear-Content)\b", re.IGNORECASE)


def classify(tool: str, args: dict) -> Optional[str]:
    """Returns a human-readable reason if this call needs confirmation, else None."""
    args = args or {}
    if tool.startswith("settings_") or tool == "skill_theme":
        return f"Change a Windows setting ({tool} {args})"
    if tool.startswith("delegate") or tool.startswith("cloud"):
        return f"Send this task to an external agent ({tool})"
    if tool in ("gmail_send", "email_send"):
        return "Send an email"
    from seguro.tools.files import normalize_user_path
    from seguro.core.workflow.node_context import is_own_artifact
    target = None
    if tool == "files_write":
        target = normalize_user_path(str(args.get("path", "")))
    elif tool == "files_move":
        target = normalize_user_path(str(args.get("dst", "")))
    elif tool in OFFICE_OUTPUT_EXT and args.get("filename"):
        from seguro.tools.office import _out_path
        target = _out_path(str(args["filename"]), OFFICE_OUTPUT_EXT[tool])
    if target and os.path.isfile(target) and not is_own_artifact(target):
        return f"Overwrite existing file {target}"
    if tool in ("run_powershell", "run_python"):
        code = str(args.get("command") or args.get("code") or "")
        if DELETE_CODE.search(code):
            return f"Run code that deletes files: {code[:160]}"
    if tool.startswith("browser_page_click") or tool.startswith("browser_page_press"):
        target = " ".join(str(v) for v in args.values())
        if RISKY_WORDS.search(target):
            return f"Click/press a control that may send, post, buy or delete: {target[:120]}"
    return None


Responder = Callable[[str, dict], Awaitable[bool]]


async def _console_responder(reason: str, details: dict) -> bool:
    if not sys.stdin or not sys.stdin.isatty():
        print(f"🛑 [CONFIRM] No interactive console to approve: {reason} -> DENIED")
        return False
    try:
        answer = await asyncio.to_thread(input, f"\n⚠️  Seguro wants to: {reason}\n   Approve? [y/N] ")
    except (EOFError, OSError):
        print("   (no console input available) -> DENIED")
        return False
    return answer.strip().lower() in ("y", "yes")


class ConfirmationBroker:
    def __init__(self):
        self.responder: Responder = _console_responder
        self.auto_approve: Set[str] = set()   # tool names pre-approved (e.g. by an eval task)
        self.timeout_s: float = 15.0

    def configure(self, responder: Optional[Responder] = None, auto_approve: Optional[Set[str]] = None,
                  timeout_s: Optional[float] = None):
        if responder is not None:
            self.responder = responder
        if auto_approve is not None:
            self.auto_approve = set(auto_approve)
        if timeout_s is not None:
            self.timeout_s = timeout_s

    async def confirm(self, reason: str, tool: Optional[str] = None, details: Optional[dict] = None) -> bool:
        details = details or {}
        bus.emit("needs_confirmation", reason=reason, tool=tool, details={k: str(v)[:200] for k, v in details.items()},
                 timeout_s=self.timeout_s)
        if tool and tool in self.auto_approve and tool not in NEVER_AUTO_APPROVE:
            approved, how = True, "pre-approved"
        else:
            try:
                approved = await asyncio.wait_for(self.responder(reason, details), timeout=self.timeout_s)
                how = "user"
            except asyncio.TimeoutError:
                approved, how = False, "timeout"
        bus.emit("confirmation_resolved", reason=reason, tool=tool, approved=approved, how=how)
        return approved


broker = ConfirmationBroker()
