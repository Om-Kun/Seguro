import os
import sys
import json
import time
from typing import Dict, Any, Optional, List
import pyautogui
from seguro.tools.registry import registry
from seguro.tools.vision import capture_screen_pil, image_to_base64, scale_coords_vlm_to_screen
from seguro.tools.input import click_xy, type_text, hotkey, scroll
from seguro.safety.gate import safety_gate

class CloudCUAClient:
    """
    Cloud Computer-Use Agent Escalation Loop.
    Executes when local perception is stuck or UIA-poor.
    Uses Anthropic Messages API computer-use tool (claude-3-5-sonnet-20241022).
    """

    def __init__(self, api_key: Optional[str] = None, max_steps: int = 30, spend_cap: float = 2.0):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.max_steps = max_steps
        self.spend_cap = spend_cap
        self.steps_used = 0
        self.total_tokens = 0

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    async def execute_subgoal(self, original_task: str, current_subgoal: str, error_context: str) -> dict:
        """
        Executes a GUI subgoal via cloud computer-use agent.
        """
        if not self.is_available():
            return {
                "ok": False,
                "error": "Cloud computer-use escalation requires ANTHROPIC_API_KEY. Local failure reported honestly without fabricated outputs.",
                "escalated": False
            }

        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=self.api_key)
        except ImportError:
            return {
                "ok": False,
                "error": "Package 'anthropic' is not installed for cloud computer-use.",
                "escalated": False
            }

        screen_w, screen_h = pyautogui.size()
        target_w, target_h = 1280, 720  # Recommended CUA resolution

        system_prompt = (
            "You are an expert Windows 11 GUI operator assisting Seguro. "
            "Complete the requested subgoal accurately using computer use tools. "
            "Do not perform unauthorized destructive actions."
        )

        initial_instruction = (
            f"Original Goal: {original_task}\n"
            f"Current Subgoal: {current_subgoal}\n"
            f"Previous Local Error: {error_context}\n"
            "Take a screenshot and perform the necessary UI actions to accomplish this subgoal."
        )

        messages = [
            {"role": "user", "content": initial_instruction}
        ]

        tools = [
            {
                "type": "computer_20241022",
                "name": "computer",
                "display_width_px": target_w,
                "display_height_px": target_h
            }
        ]

        step = 0
        while step < self.max_steps:
            step += 1
            self.steps_used += 1

            # Check spend budget cap (estimate tokens)
            if self.steps_used * 0.03 > self.spend_cap:
                return {
                    "ok": False,
                    "error": f"Cloud session spend cap (${self.spend_cap}) reached.",
                    "steps": step
                }

            try:
                response = await client.beta.messages.create(
                    model="claude-3-5-sonnet-20241022",
                    max_tokens=1024,
                    system=system_prompt,
                    messages=messages,
                    tools=tools,
                    betas=["computer-use-2024-10-22"]
                )
            except Exception as e:
                return {"ok": False, "error": f"Anthropic API error: {e}", "steps": step}

            tool_calls = [c for c in response.content if getattr(c, "type", "") == "tool_use"]
            if not tool_calls:
                # Text conclusion
                text_content = "".join([getattr(c, "text", "") for c in response.content])
                return {
                    "ok": True,
                    "evidence": text_content or "Cloud CUA completed subgoal.",
                    "steps": step,
                    "escalated": True
                }

            # Process computer use action
            tool_use = tool_calls[0]
            action = tool_use.input.get("action")
            tool_res_content = []

            if action == "screenshot":
                img = capture_screen_pil()
                b64 = image_to_base64(img, max_dim=target_w)
                tool_res_content = [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64
                        }
                    }
                ]

            elif action in ("left_click", "right_click", "double_click", "mouse_move"):
                coord = tool_use.input.get("coordinate", [0, 0])
                px, py = scale_coords_vlm_to_screen(coord[0], coord[1], (target_w, target_h), (screen_w, screen_h))
                if action == "left_click":
                    click_xy(px, py)
                elif action == "right_click":
                    click_xy(px, py, right=True)
                elif action == "double_click":
                    click_xy(px, py, double=True)
                else:
                    pyautogui.moveTo(px, py, duration=0.1)
                tool_res_content = [{"type": "text", "text": f"Performed {action} at ({px}, {py})"}]

            elif action == "type":
                text_val = tool_use.input.get("text", "")
                type_text(text_val)
                tool_res_content = [{"type": "text", "text": f"Typed '{text_val}'"}]

            elif action == "key":
                key_combo = tool_use.input.get("text", "").split("+")
                hotkey(key_combo)
                tool_res_content = [{"type": "text", "text": f"Pressed hotkey {key_combo}"}]

            # Add to thread
            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": tool_res_content
                    }
                ]
            })

        return {"ok": False, "error": "Cloud CUA reached max steps without completing subgoal.", "steps": step}

cloud_cua = CloudCUAClient()
