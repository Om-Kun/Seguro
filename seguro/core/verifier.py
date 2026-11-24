import os
import re
import time
import zipfile
from typing import Dict, Any, Optional

# Tools that only read state. They "verify" when they return data, but never count as
# having changed anything the user asked for.
OBSERVE_TOOLS = {
    "windows_list", "files_read", "files_find", "files_list", "uia_snapshot",
    "look_at_screen", "ask_user", "browser_get_title", "news_search", "web_fetch", "browser_page_text",
    "vision_ask", "webcam_detect_objects",
}

# Input actions with no code-level way to confirm their effect. They are never "verified";
# a later state check (file on disk, window title, registry value) has to prove the outcome.
UNVERIFIABLE_TOOLS = {
    "type_text", "hotkey", "click_xy", "scroll", "uia_click", "uia_set_text", "ground_and_click",
    "browser_page_click",
}

BROWSER_TOOLS = {
    "browser_open", "browser_search", "google_sheets_open", "google_docs_open",
    "google_slides_open", "skill_youtube_player",
}


def _payload(result: dict) -> dict:
    """Tool results arrive wrapped as {ok, data, error}; checks read the tool's own payload."""
    data = result.get("data") if isinstance(result, dict) else None
    if isinstance(data, dict):
        return data
    return result if isinstance(result, dict) else {}


def _norm(p: str) -> str:
    from seguro.tools.files import normalize_user_path
    return normalize_user_path(p)


def _v(verified: bool, evidence: str, kind: str = "act", warning: Optional[str] = None) -> dict:
    return {"verified": verified, "evidence": evidence, "warning": warning, "kind": kind}


class ActionVerifier:
    """
    Post-action verification engine.
    Confirms in code that a tool call produced the expected system change.
    Anything it cannot confirm is reported as unverified, never as verified.
    """

    def verify_action(self, tool: str, args: dict, result: dict, state=None) -> dict:
        """Returns {verified, evidence, warning, kind} where kind is act|observe|control."""
        args = args or {}
        if tool == "task_complete":
            try:
                return self._verify_task_complete(args, state)
            except Exception as e:
                return _v(False, f"Verifier exception on task_complete: {e}", "control", str(e))

        if tool == "node_output":
            ok = not (isinstance(result, dict) and result.get("ok") is False)
            return _v(ok, f"Output '{args.get('key')}' {'accepted' if ok else 'rejected: ' + str(result.get('error'))}",
                      "control")

        kind = "observe" if tool in OBSERVE_TOOLS else "act"

        if isinstance(result, dict) and result.get("ok") is False:
            data = _payload(result)
            err = result.get("error") or data.get("error") or data.get("stderr") or "unknown error"
            return _v(False, f"Tool '{tool}' failed: {str(err)[:300]}", kind)

        try:
            if tool in OBSERVE_TOOLS:
                return _v(True, f"Observation from '{tool}' returned data.", "observe")
            if tool in UNVERIFIABLE_TOOLS:
                return _v(False, f"Unverified: '{tool}' ran, but its effect can't be checked in code. "
                                 f"Confirm the outcome with a state check (file, window title, value).",
                          "act", warning="unverifiable_input_action")
            if tool == "apps_launch":
                return self._verify_apps_launch(args, result)
            if tool in ("files_write", "files_mkdir", "chart_png", "browser_page_screenshot", "webcam_capture",
                        "portrait_create", "portrait_paint_in_paint"):
                return self._verify_file_op(args, result)
            if tool == "files_move":
                return self._verify_file_move(args, result)
            if tool in ("excel_create_table_and_chart", "word_create_document", "skill_report_generator",
                        "pptx_create"):
                return self._verify_office_doc(tool, args, result)
            if tool in ("run_powershell", "run_python"):
                return self._verify_script_exec(tool, args, result)
            if tool in ("windows_focus", "windows_maximize"):
                return self._verify_window_status(args, result, ("focused", "maximized"))
            if tool == "windows_close":
                return self._verify_window_close(args, result)
            if tool in BROWSER_TOOLS:
                return self._verify_browser(args, result)
            if tool in ("browser_page_open", "browser_page_fill", "process_start", "process_stop",
                        "gmail_create_draft"):
                return self._verify_page_and_process(tool, args, result)
            if tool == "settings_set_theme":
                return self._verify_theme(args, result)
            if tool.startswith("skill_") and "checks" in _payload(result):
                data = _payload(result)
                failed = [c for c in data["checks"] if not c.get("ok")]
                if data.get("ok") and not failed:
                    return _v(True, f"{data.get('summary')} | {len(data['checks'])} checks passed in {data.get('seconds')}s: "
                                    + ", ".join(os.path.basename(str(c.get('path') or c.get('check'))) for c in data["checks"]))
                return _v(False, f"Skill checks failed: {data.get('error') or failed}")
            if tool in ("skill_fastapi_generator", "delegate_to_coding_agent"):
                return self._verify_project_files(args, result)
            return _v(False, f"Unverified: no verifier is defined for '{tool}'.", "act",
                      warning="no_verifier")
        except Exception as e:
            return _v(False, f"Verifier exception on '{tool}': {e}", kind, str(e))

    def _verify_apps_launch(self, args: dict, result: dict) -> dict:
        from seguro.tools.windows import windows_list
        data = _payload(result)
        app_name = (args.get("name") or "").lower().strip()
        synonyms = {app_name}
        if data.get("matched_name"):
            synonyms.add(str(data["matched_name"]).lower())
        if "calc" in app_name: synonyms.update(["calculator"])
        if "cmd" in app_name: synonyms.update(["command prompt", "cmd.exe"])
        if "terminal" in app_name: synonyms.update(["terminal", "powershell", "command prompt"])
        if "snipping" in app_name: synonyms.update(["snipping"])
        if "explorer" in app_name: synonyms.update(["file explorer", "this pc", "home"])
        if "task manager" in app_name or "taskmgr" in app_name: synonyms.update(["task manager"])
        if "paint" in app_name: synonyms.update(["paint"])
        synonyms.discard("")

        for _ in range(8):
            time.sleep(0.4)
            for w in windows_list():
                title = w["title"].lower()
                if any(syn in title for syn in synonyms):
                    return _v(True, f"Verified: window '{w['title']}' is open.")
        return _v(False, f"Launch was dispatched for '{app_name}', but no matching window appeared within 3.2s.")

    def _verify_file_op(self, args: dict, result: dict) -> dict:
        data = _payload(result)
        path = data.get("path") or (args.get("path") and _norm(args["path"]))
        if not path:
            return _v(False, "No file path provided in args or result.")
        if os.path.exists(path):
            if os.path.isdir(path):
                return _v(True, f"Folder verified on disk: '{path}'.")
            from seguro.core.workflow.blackboard import validate_file_content, OutputTypeError
            try:
                validate_file_content(path)
            except OutputTypeError as e:
                return _v(False, f"File exists but its content is wrong: {e}")
            return _v(True, f"File verified on disk: '{path}' (size: {os.path.getsize(path)} bytes).")
        return _v(False, f"Expected file does not exist on disk: '{path}'.")

    def _verify_file_move(self, args: dict, result: dict) -> dict:
        data = _payload(result)
        moved = data.get("moved") or []
        if not moved:
            return _v(False, "files_move reported no files moved.")
        bad = [m for m in moved if not os.path.exists(m["dst"]) or os.path.exists(m["src"])]
        if bad:
            return _v(False, f"Move not reflected on disk for: {[b['src'] for b in bad]}")
        return _v(True, f"Verified {len(moved)} file(s) now at destination: "
                        f"{', '.join(m['dst'] for m in moved[:5])}{' …' if len(moved) > 5 else ''}")

    def _verify_office_doc(self, tool: str, args: dict, result: dict) -> dict:
        data = _payload(result)
        path = data.get("path")
        if not path:
            return _v(False, f"{tool} returned no output path.")
        if not os.path.exists(path):
            return _v(False, f"Office document not found at '{path}'.")
        size = os.path.getsize(path)
        if size < 500:
            return _v(False, f"Office document at '{path}' is abnormally small ({size} bytes).")
        try:
            with zipfile.ZipFile(path, "r") as zf:
                bad = zf.testzip()
                if bad is not None:
                    return _v(False, f"Corrupt ZIP entry '{bad}' in {path}")
        except zipfile.BadZipFile:
            return _v(False, f"'{path}' is not a valid OOXML package.")
        detail = ""
        if path.lower().endswith(".pptx"):
            from pptx import Presentation
            detail = f", {len(Presentation(path).slides)} slide(s)"
        if path.lower().endswith(".xlsx"):
            import openpyxl
            ws = openpyxl.load_workbook(path).active
            detail = f", {ws.max_row - 1} data rows, {len(ws._charts)} chart(s)"
        return _v(True, f"Verified '{path}' ({size} bytes, valid OOXML{detail}).")

    def _verify_script_exec(self, tool: str, args: dict, result: dict) -> dict:
        data = _payload(result)
        rc = data.get("returncode")
        if rc is None:
            return _v(False, f"{tool} returned no exit code.")
        if rc != 0:
            return _v(False, f"Script failed with return code {rc}. Stderr: {str(data.get('stderr', ''))[:200]}")
        out = str(data.get("stdout", ""))
        # Exit code 0 proves the script ran, not that it achieved the goal.
        return _v(True, f"Script exited 0. Stdout: {out[:300]!r}")

    def _verify_window_status(self, args: dict, result: dict, good: tuple) -> dict:
        data = _payload(result)
        if data.get("status") in good:
            return _v(True, f"Window '{data.get('title')}' is {data.get('status')}.")
        return _v(False, f"Window action failed: {data.get('status')} ({args.get('title_contains')})")

    def _verify_window_close(self, args: dict, result: dict) -> dict:
        from seguro.tools.windows import windows_list
        data = _payload(result)
        if data.get("status") != "closed":
            return _v(False, f"No window matching '{args.get('title_contains')}' was closed ({data.get('status')}).")
        title = data.get("title", "")
        for _ in range(5):
            time.sleep(0.3)
            if not any(w["title"] == title for w in windows_list()):
                return _v(True, f"Window '{title}' is gone.")
        return _v(False, f"Sent close to '{title}', but it is still open (maybe an unsaved-changes prompt).")

    def _verify_browser(self, args: dict, result: dict) -> dict:
        data = _payload(result)
        title = data.get("window_title")
        if title:
            return _v(True, f"Browser window shows page titled: '{title}'")
        return _v(False, f"Browser launch dispatched to {data.get('url')}, but no browser window title "
                         f"confirmed the page loaded.")

    def _verify_page_and_process(self, tool: str, args: dict, result: dict) -> dict:
        data = _payload(result)
        if tool == "browser_page_open":
            if data.get("title") or data.get("text"):
                return _v(True, f"Page loaded: {data.get('url')} titled '{data.get('title')}'")
            return _v(False, f"Page at {data.get('url')} loaded with no title or text.")
        if tool == "browser_page_fill":
            ok = str(data.get("value")) == str(args.get("text"))
            return _v(ok, f"Field '{args.get('target')}' now contains {data.get('value')!r}")
        if tool == "process_start":
            import psutil
            alive = psutil.pid_exists(data.get("pid", -1))
            port = data.get("port")
            if alive and port:
                from seguro.tools.process import _port_open
                alive = _port_open(port)
            return _v(alive, f"Process '{data.get('name')}' pid {data.get('pid')} "
                             f"{'running' if alive else 'not running'}{f', port {port} open' if alive and port else ''}")
        if tool == "process_stop":
            return _v(True, f"Process '{data.get('name')}' stopped (exit {data.get('exit_code')}).")
        if tool == "gmail_create_draft":
            if data.get("draft_found"):
                return _v(True, f"Gmail draft '{data.get('subject')}' to {data.get('to')} found in Drafts "
                                f"with attachments {data.get('attachments')}.")
            return _v(False, "Gmail draft not confirmed in Drafts.")
        return _v(False, f"Unverified: {tool}")

    def _verify_theme(self, args: dict, result: dict) -> dict:
        import winreg
        data = _payload(result)
        want_light = 0 if data.get("mode") == "dark" else 1
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        vals = {n: winreg.QueryValueEx(k, n)[0] for n in data.get("keys_written", [])}
        if vals and all(v == want_light for v in vals.values()):
            return _v(True, f"Registry confirms {data.get('mode')} mode: {vals}")
        return _v(False, f"Registry does not show {data.get('mode')} mode: {vals}")

    def _verify_project_files(self, args: dict, result: dict) -> dict:
        data = _payload(result)
        workdir = data.get("workdir")
        files = data.get("files") or []
        if workdir and files and all(os.path.exists(os.path.join(workdir, f)) for f in files):
            return _v(True, f"Verified {len(files)} file(s) in '{workdir}'.")
        return _v(False, f"Expected project files not found in '{workdir}'.")

    def _verify_task_complete(self, args: dict, state=None) -> dict:
        evidence_str = str(args.get("evidence") or "").strip()
        invalid_placeholders = {"none", "null", "n/a", "empty", "no evidence", "undefined", ""}
        if evidence_str.lower() in invalid_placeholders or len(evidence_str) < 5:
            return _v(False, f"Task complete rejected: '{evidence_str}' is placeholder/insufficient evidence.",
                      "control")

        raw_paths = re.findall(r'(?:[A-Za-z]:\\[^\s\'"]+|~[\\/][^\s\'"]+)', evidence_str)
        verified_files, missing_files = [], []
        for raw_p in raw_paths:
            real_p = os.path.abspath(os.path.expanduser(raw_p.replace("\\\\", "\\").rstrip(".,;:)]}`")))
            if os.path.isfile(real_p) and os.path.getsize(real_p) > 0:
                verified_files.append(real_p)  # only real files count as proof; folders like Desktop prove nothing
            elif not os.path.exists(real_p):
                missing_files.append(real_p)
        if missing_files:
            return _v(False, f"Claimed file evidence failed verification: missing {missing_files}", "control",
                      "Hallucinated or nonexistent file.")

        history = list(getattr(state, "history", None) or [])
        checks = [(s.tool, s.verifier_check or {}) for s in history if s.tool != "task_complete"]
        acts = [(t, c) for t, c in checks if c.get("kind") == "act"]
        observes = [(t, c) for t, c in checks if c.get("kind") == "observe" and c.get("verified")]

        if state is None:
            if verified_files:
                return _v(True, f"Task complete verified with files on disk: {verified_files}", "control")
            return _v(False, "Task complete rejected: no verified step or on-disk evidence.", "control")

        # A failed action must be fixed by a later verified call of the same tool; an
        # unrelated verified action does not cancel it out.
        unresolved = []
        for i, (t, c) in enumerate(acts):
            if not c.get("verified") and c.get("warning") != "unverifiable_input_action":
                if not any(t2 == t and c2.get("verified") for t2, c2 in acts[i + 1:]):
                    unresolved.append(t)
        if unresolved and not verified_files:
            return _v(False, f"Task complete rejected: failed step(s) never succeeded on retry: "
                             f"{sorted(set(unresolved))}.", "control")

        if acts:
            last_tool, last_check = acts[-1]
            if last_check.get("verified"):
                return _v(True, f"Last action '{last_tool}' verified: {last_check.get('evidence')}", "control")
            if verified_files:
                return _v(True, f"Last action '{last_tool}' was unverified, but claimed files exist: "
                                f"{verified_files}", "control")
            return _v(False, f"Task complete rejected: the last action '{last_tool}' was not verified "
                             f"({last_check.get('evidence')}). Prove the end state first.", "control")
        if observes:
            return _v(True, f"Answer based on verified observation from '{observes[-1][0]}'.", "control")
        return _v(False, "Task complete rejected: no tool call has verified anything yet.", "control")

verifier = ActionVerifier()
