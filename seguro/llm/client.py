import os
import yaml
from typing import List, Dict, Any, Optional
from seguro.llm.providers.local_openai_compat import LocalOpenAICompatProvider, LLMResponse

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")


def load_config(config_path: str = None) -> dict:
    path = config_path or CONFIG_PATH
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


class LLMClient:
    def __init__(self, config_path: str = None, role: str = "brain", provider: LocalOpenAICompatProvider = None):
        self.config = load_config(config_path)
        self.role = role
        if provider is not None:
            self.provider = provider
            return
        cfg = self.config.get("llm", {}).get(role, {})
        if cfg.get("provider", "local") not in ("local", "openai_compat"):
            raise ValueError(f"llm.{role}.provider '{cfg.get('provider')}' is not supported by this client")
        self.provider = LocalOpenAICompatProvider.from_config(cfg)

    @classmethod
    def escalation(cls, config_path: str = None) -> Optional["LLMClient"]:
        """Cloud brain for hybrid mode, or None if not configured / its API key is missing."""
        cfg = load_config(config_path).get("llm", {}).get("escalation")
        if not cfg or not cfg.get("enabled", True):
            return None
        if cfg.get("api_key_env") and not os.getenv(cfg["api_key_env"]):
            return None
        return cls(config_path, role="escalation")

    @property
    def model(self) -> str:
        return self.provider.model

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024
    ) -> LLMResponse:
        return await self.provider.chat(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens
        )

    async def complete_json(self, messages, schema: dict, name: str = "result", temperature: float = 0.0,
                            max_tokens: int = 2048) -> str:
        return await self.provider.complete_json(messages, schema, name=name, temperature=temperature,
                                                 max_tokens=max_tokens)
