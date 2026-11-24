import os
import json
import time
import asyncio
from typing import Dict, Any, List, Optional

# Ensure all tool modules are imported so registry is fully populated
import seguro.tools.apps
import seguro.tools.windows
import seguro.tools.shell
import seguro.tools.pyexec
import seguro.tools.files
import seguro.tools.office
import seguro.tools.browser
import seguro.tools.uia
import seguro.tools.input
import seguro.tools.vision
import seguro.tools.control
import seguro.tools.delegate
import seguro.tools.charts
import seguro.tools.web
import seguro.tools.slides
import seguro.tools.process
import seguro.tools.webpage
import seguro.tools.gmail
import seguro.tools.webcam
import seguro.tools.zomato
import seguro.skills.library.demo_skills
from seguro.skills.library.demo_skills import match_skill
import seguro.skills.library.fastapi_generator
import seguro.skills.library.youtube_player
import seguro.skills.library.report_generator
import seguro.skills.library.settings_theme

from seguro.tools.registry import registry
from seguro.safety.killswitch import killswitch
from seguro.safety.gate import safety_gate
from seguro.llm.client import LLMClient
from seguro.core.prompts import BRAIN_SYSTEM_PROMPT
from seguro.core.state import TaskState
from seguro.core.verifier import verifier
from seguro.core.router import fast_router
from seguro.core.events import bus

class Orchestrator:
    """
    Master ReAct orchestrator for Seguro V2.
    Implements:
      1. Fast-path routing (<300ms) for unambiguous intents
      2. 6-rung execution ladder ReAct reasoning
      3. Strict post-action verification (Never claim success without evidence)
      4. Safe isolated subprocess dispatch & safety gating
      5. Context management & history trimming (<8k tokens)
    """

    def __init__(self, config_path: str = None):
        self.llm = LLMClient(config_path)
        self.config = self.llm.config
        self.mode = self.config.get("mode", "hybrid")
        self.max_steps = self.config.get("limits", {}).get("max_steps", 25)
        self.escalate_threshold = self.config.get("limits", {}).get("escalate_after_failures", 2)

    async def run_task(self, prompt: str, mode: Optional[str] = None, llm: Optional[LLMClient] = None,
                       system_extra: Optional[str] = None, max_steps: Optional[int] = None,
                       use_router: bool = True) -> TaskState:
        """Single-task ReAct loop. Workflow nodes pass a scoped prompt plus `system_extra`, an
        optional escalation `llm`, and a smaller step budget."""
        active_mode = mode or self.mode
        llm = llm or self.llm
        state = TaskState(request=prompt, mode=active_mode, max_steps=max_steps or self.max_steps)

        # 1. Check F12 killswitch before starting
        if killswitch.is_triggered():
            state.abort("Killswitch active at task start")
            return state

        # 2a. Verified skills: known hard jobs run deterministically and check their own outputs
        skill = match_skill(prompt) if use_router else None
        if skill:
            print(f"[Router] Verified skill: {skill}")
            tool_res = await registry.dispatch({"tool": skill, "args": {"request": prompt}}, timeout=600, state=state)
            v_check = verifier.verify_action(skill, {"request": prompt}, tool_res, state=state)
            state.record_step(skill, {"request": prompt}, tool_res, v_check)
            self._emit_tool_call(skill, {"request": prompt}, tool_res, v_check)
            if v_check["verified"]:
                state.success(evidence=v_check["evidence"])
            else:
                state.give_up(reason=v_check["evidence"])
            return state

        # 2b. Fast-Path Router check (< 300ms)
        fast_spec = fast_router.match(prompt) if use_router else None
        if fast_spec:
            tool_name = fast_spec["tool"]
            tool_args = fast_spec["args"]
            print(f"[Router] Fast path triggered: {tool_name}({tool_args})")

            tool_res = await registry.dispatch({"tool": tool_name, "args": tool_args}, state=state)
            v_check = verifier.verify_action(tool_name, tool_args, tool_res, state=state)
            state.record_step(tool_name, tool_args, tool_res, v_check)
            self._emit_tool_call(tool_name, tool_args, tool_res, v_check)

            if v_check["verified"]:
                state.success(evidence=v_check["evidence"])
                return state
            else:
                print(f"[Router] Fast path unverified: {v_check['evidence']}, falling back to Brain ReAct loop.")

        # 3. Brain ReAct Loop
        messages = [
            {"role": "system", "content": BRAIN_SYSTEM_PROMPT + ("\n\n" + system_extra if system_extra else "")},
            {"role": "user", "content": prompt}
        ]

        text_only_replies = 0
        rejected_completions = 0
        provider_errors = 0

        while state.status == "running" and state.step_count < state.max_steps:
            if killswitch.is_triggered():
                state.abort("F12 Killswitch triggered during execution")
                break

            # Trim history to keep context compact (< 8k tokens)
            trimmed_messages = self._trim_history(messages)

            # Call LLM with tool catalogue
            available_tools = registry.get_openai_tools()
            llm_res = await llm.chat(trimmed_messages, tools=available_tools)

            if killswitch.is_triggered():
                state.abort("F12 Killswitch triggered after LLM call")
                break

            if llm_res.content.startswith("[Local Provider Error]"):
                provider_errors += 1
                print(f"[Orchestrator] {llm_res.content}")
                if provider_errors >= 2:
                    state.give_up(reason=f"LLM server unavailable: {llm_res.content}")
                    return state
                continue

            # Some local models emit the tool call as JSON text instead of a structured call.
            if not llm_res.tool_calls:
                text_call = self._extract_text_tool_call(llm_res.content)
                if text_call:
                    llm_res.tool_calls = [text_call]
                    llm_res.content = ""

            # Handle Tool Calls
            if llm_res.tool_calls:
                text_only_replies = 0
                for tc in llm_res.tool_calls:
                    tool_name = tc.get("tool")
                    tool_args = tc.get("args") or {}
                    call_id = tc.get("id", f"call_{state.step_count}")

                    # A. Check completion / failure tools
                    if tool_name == "task_complete":
                        ev = str(tool_args.get("evidence", ""))
                        v_check = verifier.verify_action("task_complete", tool_args, {"ok": True}, state=state)
                        state.record_step(tool_name, tool_args, {"ok": True, "status": "complete"}, v_check)

                        if v_check["verified"]:
                            state.success(evidence=f"{ev} [verifier: {v_check['evidence']}]")
                            return state
                        rejected_completions += 1
                        if rejected_completions >= 3:
                            state.give_up(reason=f"Completion rejected 3 times by verifier. Last: {v_check['evidence']}")
                            return state
                        # Re-inject failure feedback so model can correct itself
                        err_msg = f"Task completion denied by verifier: {v_check['evidence']}. Fix the issue before finishing."
                        messages.append({"role": "assistant", "content": f"Attempting task_complete with: {ev}"})
                        messages.append({"role": "user", "content": f"[System Verifier Feedback]: {err_msg}"})
                        break

                    elif tool_name == "task_failed":
                        reason = tool_args.get("reason", "Unknown reason")
                        state.record_step(tool_name, tool_args, {"ok": True, "status": "failed"}, None)
                        state.give_up(reason=reason)
                        return state

                    # B. Normal tool dispatch
                    tool_res = await registry.dispatch({"tool": tool_name, "args": tool_args}, state=state)
                    v_check = verifier.verify_action(tool_name, tool_args, tool_res, state=state)
                    state.record_step(tool_name, tool_args, tool_res, v_check)
                    self._emit_tool_call(tool_name, tool_args, tool_res, v_check)

                    # Append to conversation history
                    assistant_msg = {
                        "role": "assistant",
                        "content": llm_res.content or f"Calling tool {tool_name}",
                        "tool_calls": [{
                            "id": call_id,
                            "type": "function",
                            "function": {"name": tool_name, "arguments": json.dumps(tool_args)}
                        }]
                    }
                    tool_output_msg = {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": tool_name,
                        "content": self._tool_message(tool_res, v_check)
                    }
                    messages.append(assistant_msg)
                    messages.append(tool_output_msg)

                    # Consecutive failure check & escalation guard
                    if state.consecutive_failures >= self.escalate_threshold:
                        if state.mode == "hybrid":
                            esc_llm = LLMClient.escalation()
                            if esc_llm is not None and llm != esc_llm:
                                print(f"[Orchestrator] {state.consecutive_failures} consecutive failures. "
                                      f"Escalating to cloud brain ({esc_llm.model}).")
                                llm = esc_llm
                                state.escalated = True
                            else:
                                print(f"[Orchestrator] {state.consecutive_failures} consecutive failures. "
                                      f"Escalation brain not available; continuing locally.")
                        else:
                            print(f"[Orchestrator] LOCAL-ONLY mode: {state.consecutive_failures} failures. Cannot escalate to cloud.")

            else:
                # A plain-text reply is only accepted as a final answer if the verifier
                # accepts it as task_complete evidence. Keywords like "done" prove nothing.
                content = llm_res.content.strip()
                messages.append({"role": "assistant", "content": content})
                text_only_replies += 1

                has_verified_act = any(
                    (s.verifier_check or {}).get("kind") == "act" and (s.verifier_check or {}).get("verified")
                    for s in state.history
                )
                if content and has_verified_act:
                    v_check = verifier.verify_action("task_complete", {"evidence": content}, {"ok": True}, state=state)
                    if v_check["verified"]:
                        state.record_step("task_complete", {"evidence": content, "implicit": True},
                                          {"ok": True, "status": "complete"}, v_check)
                        state.success(evidence=f"{content} [verifier: {v_check['evidence']}]")
                        return state

                if text_only_replies >= 3:
                    state.give_up(reason=f"Model stopped calling tools without a verified result. Last reply: {content[:200]}")
                    return state
                messages.append({
                    "role": "user",
                    "content": "Nothing has been verified yet. Call a tool to make progress, then call "
                               "task_complete with concrete evidence, or task_failed with the honest reason."
                })

        if state.status == "running":
            state.give_up(reason=f"Max step budget ({state.max_steps}) reached without verified completion.")

        return state

    @staticmethod
    def _emit_tool_call(tool_name: str, tool_args: dict, tool_res: dict, v_check: dict):
        bus.emit("tool_call", tool=tool_name, args={k: str(v)[:200] for k, v in (tool_args or {}).items()},
                 ok=tool_res.get("ok"), verified=v_check.get("verified"), evidence=v_check.get("evidence"),
                 elapsed_ms=tool_res.get("elapsed_ms"))

    @staticmethod
    def _extract_text_tool_call(content: str) -> Optional[dict]:
        """Finds a {"name": <registered tool>, "parameters"|"arguments": {...}} object in text."""
        if not content or '"name"' not in content:
            return None
        decoder = json.JSONDecoder()
        idx = content.find("{")
        while idx != -1:
            try:
                obj, _ = decoder.raw_decode(content, idx)
            except ValueError:
                obj = None
            if isinstance(obj, dict) and obj.get("name") in registry._tools:
                args = obj.get("parameters", obj.get("arguments", {}))
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                return {"id": f"text_call_{int(time.time() * 1000)}", "tool": obj["name"],
                        "args": args if isinstance(args, dict) else {}}
            idx = content.find("{", idx + 1)
        return None

    @staticmethod
    def _tool_message(tool_res: dict, v_check: dict, limit: int = 3000) -> str:
        """Compact tool result for the model: drops binary blobs and caps size."""
        def scrub(o):
            if isinstance(o, dict):
                return {k: ("<omitted>" if k.endswith("_b64") else scrub(v)) for k, v in o.items()}
            if isinstance(o, list):
                return [scrub(v) for v in o[:60]]
            if isinstance(o, str) and len(o) > 1500:
                return o[:1500] + "…"
            return o
        msg = json.dumps({"result": scrub(tool_res),
                          "verification": {"verified": v_check.get("verified"), "evidence": v_check.get("evidence")}},
                         default=str)
        return msg if len(msg) <= limit else msg[:limit] + "…(truncated)"

    def _trim_history(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Trims message history to keep token budget < 8k.
        Keeps system prompt, initial user prompt, and last 6 messages.
        Older messages are summarized to compact one-liners.
        """
        if len(messages) <= 8:
            return messages

        system_msg = messages[0]
        initial_user = messages[1]
        recent_messages = messages[-6:]

        summary_parts = []
        for msg in messages[2:-6]:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "tool":
                summary_parts.append(f"[Step tool result: {msg.get('name')}]")
            elif role == "assistant" and msg.get("tool_calls"):
                tools_called = [tc["function"]["name"] for tc in msg.get("tool_calls", [])]
                summary_parts.append(f"[Assistant invoked {', '.join(tools_called)}]")
            elif content:
                summary_parts.append(f"[{role}: {content[:60]}...]")

        summary_msg = {
            "role": "user",
            "content": f"[History Summary of earlier steps]: " + " -> ".join(summary_parts)
        }

        return [system_msg, initial_user, summary_msg] + recent_messages

orchestrator = Orchestrator()
