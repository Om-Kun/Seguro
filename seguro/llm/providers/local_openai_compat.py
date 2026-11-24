import asyncio
import json
import os
from openai import AsyncOpenAI, APIStatusError, APIConnectionError
from typing import List, Dict, Any, Optional

class LLMResponse:
    def __init__(self, content: str = "", tool_calls: list = None, raw: Any = None):
        self.content = content or ""
        self.tool_calls = tool_calls or []
        self.raw = raw

    @property
    def is_final(self) -> bool:
        # If no tool calls, it's a direct conversational completion or final response
        return len(self.tool_calls) == 0

class LocalOpenAICompatProvider:
    """Any OpenAI-compatible chat endpoint: local Ollama, Ollama Cloud models, Groq, OpenRouter."""

    def __init__(self, base_url: str = "http://127.0.0.1:11434/v1", model: str = "os-agent:latest",
                 api_key: str = "ollama", timeout: float = 60.0, reasoning_effort: str = None):
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key or "ollama", timeout=timeout)
        self.model = model
        self.base_url = base_url
        # e.g. "none" turns off hidden thinking on reasoning models (qwen3): 34s -> 4s per tool call
        self.extra = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}

    @classmethod
    def from_config(cls, cfg: dict) -> "LocalOpenAICompatProvider":
        key = os.getenv(cfg["api_key_env"], "") if cfg.get("api_key_env") else "ollama"
        return cls(base_url=cfg.get("base_url", "http://127.0.0.1:11434/v1"),
                   model=cfg.get("model", "os-agent:latest"), api_key=key,
                   timeout=float(cfg.get("timeout_s", 60)), reasoning_effort=cfg.get("reasoning_effort"))

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024
    ) -> LLMResponse:
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **self.extra,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        for attempt in range(2):
            try:
                response = await self.client.chat.completions.create(**kwargs)
                choice = response.choices[0].message
                content = choice.content or ""

                tool_calls = []
                if choice.tool_calls:
                    for tc in choice.tool_calls:
                        try:
                            args = json.loads(tc.function.arguments)
                        except Exception:
                            args = {"raw": tc.function.arguments}
                        tool_calls.append({
                            "id": tc.id,
                            "tool": tc.function.name,
                            "args": args
                        })

                return LLMResponse(content=content, tool_calls=tool_calls, raw=response)
            except Exception as e:
                is_5xx = (isinstance(e, APIStatusError) and e.status_code >= 500) or isinstance(e, APIConnectionError) or "500" in str(e)
                if is_5xx and attempt == 0:
                    await asyncio.sleep(1.5)
                    continue
                # Return an error completion rather than crashing the caller
                return LLMResponse(content=f"[Local Provider Error] {e}", tool_calls=[])

    async def complete_json(self, messages: List[Dict[str, Any]], schema: dict, name: str = "result",
                            temperature: float = 0.0, max_tokens: int = 2048) -> str:
        """Schema-constrained completion (structured outputs). Returns the raw JSON text; the
        caller validates it. Raises on transport errors so the caller can report them."""
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": schema}},
            **self.extra,
        }
        for attempt in range(2):
            try:
                response = await self.client.chat.completions.create(**kwargs)
                return response.choices[0].message.content or ""
            except Exception as e:
                is_5xx = (isinstance(e, APIStatusError) and e.status_code >= 500) or isinstance(e, APIConnectionError) or "500" in str(e)
                if is_5xx and attempt == 0:
                    await asyncio.sleep(1.5)
                    continue
                raise
