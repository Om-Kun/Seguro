import ast
import json
import time
import inspect
import asyncio
from typing import Callable, Dict, Any, List
from seguro.safety.audit_log import audit_logger

# Parameter-name aliases small models commonly emit, keyed by the real parameter name.
# An alias is only remapped when the tool does not itself accept that name.
PARAM_ALIASES = {
    "title_contains": ["title", "window", "window_title", "name", "filename", "app", "query"],
    "path": ["file", "filename", "file_path", "filepath", "target", "folder", "directory"],
    "src": ["source", "from", "files", "file", "path", "paths"],
    "dst": ["destination", "dest", "to", "target", "folder", "target_folder", "directory"],
    "root": ["folder", "directory", "dir", "path"],
    "keys": ["key", "combo", "hotkey", "shortcut"],
    "query": ["q", "search", "text", "term"],
    "url": ["link", "address", "path", "file"],
    "name": ["app", "app_name", "application", "program"],
    "command": ["cmd", "script", "powershell"],
    "code": ["script", "python", "source"],
    "mode": ["theme", "value"],
}


def _parse_list(val):
    """Turns a stringified list ("['a','b']", '["a"]', "a, b", "ctrl+s") into a list."""
    if isinstance(val, list):
        return val
    if not isinstance(val, str):
        return [val]
    s = val.strip()
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(s)
            if isinstance(parsed, (list, tuple)):
                return list(parsed)
        except Exception:
            pass
    if "+" in s and "," not in s:
        return [p.strip() for p in s.split("+") if p.strip()]
    return [p.strip() for p in s.split(",") if p.strip()]


def _coerce_args(func: Callable, schema: dict, args: dict) -> tuple:
    """Maps aliased parameter names and coerces array params. Returns (args, error)."""
    if not isinstance(args, dict):
        return {}, f"Arguments must be an object, got {type(args).__name__}."
    sig = inspect.signature(func)
    params = sig.parameters
    accepts_kwargs = any(p.kind == p.VAR_KEYWORD for p in params.values())
    props = (schema.get("function", {}).get("parameters", {}) or {}).get("properties", {})

    out = {}
    unknown = []
    for k, v in args.items():
        if k in params or accepts_kwargs:
            out[k] = v
            continue
        mapped = next((real for real, aliases in PARAM_ALIASES.items()
                       if real in params and real not in args and real not in out and k in aliases), None)
        if mapped:
            out[mapped] = v
        else:
            unknown.append(k)

    if unknown:
        valid = ", ".join(f"{n}" for n in params)
        return out, f"Unknown argument(s) {unknown}. Valid arguments: {valid}."

    for k, v in list(out.items()):
        ptype = props.get(k, {}).get("type")
        try:
            if ptype == "array":
                items = _parse_list(v)
                # ["[\"a\", \"b\"]"] -> ["a", "b"]: a list holding one stringified list
                if len(items) == 1 and isinstance(items[0], str) and items[0].strip().startswith("["):
                    items = _parse_list(items[0])
                out[k] = items
            elif ptype == "boolean" and isinstance(v, str):
                out[k] = v.strip().lower() in ("true", "1", "yes", "y")   # "False" must not be truthy
            elif ptype == "integer" and isinstance(v, str) and v.strip().lstrip("-").isdigit():
                out[k] = int(v)
            elif ptype == "number" and isinstance(v, str):
                out[k] = float(v)
        except ValueError:
            return out, f"Argument '{k}' should be a {ptype}, got {v!r}."

    missing = [n for n, p in params.items()
               if p.default is inspect.Parameter.empty and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
               and n not in out]
    if missing:
        return out, f"Missing required argument(s) {missing}."
    return out, None


def _derive_outcome(res: Any) -> tuple:
    """Reads the tool's own payload to decide ok/error. Tools report failure in different
    shapes (ok:false, returncode!=0, status:error/not_found); all of them mean failure."""
    if not isinstance(res, dict):
        return True, None
    err = res.get("error") or res.get("message")
    if "ok" in res:
        ok = bool(res["ok"])
    elif "returncode" in res:
        ok = res["returncode"] == 0
        err = err or (res.get("stderr") or None)
    elif res.get("status") in ("error", "not_found", "failed", "denied"):
        ok = False
        err = err or f"status={res.get('status')}"
    else:
        ok = True
    return ok, (err if not ok else None)


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Callable] = {}
        self._schemas: Dict[str, dict] = {}
        self._descriptions: Dict[str, str] = {}

    def register(self, name: str, description: str, parameters: dict):
        """Decorator to register an executable tool with JSON schema."""
        def decorator(func: Callable):
            self._tools[name] = func
            self._descriptions[name] = description
            self._schemas[name] = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters
                }
            }
            return func
        return decorator

    def get_openai_tools(self) -> List[dict]:
        """Returns standard OpenAI tools array."""
        return list(self._schemas.values())

    async def dispatch(self, call_spec: dict, timeout: float = 30.0, state=None) -> dict:
        """
        Dispatches a tool call safely.
        Returns {ok, data, error, elapsed_ms}; `ok` reflects the tool's own outcome, not just
        "the function returned". Never raises into the loop.
        """
        tool_name = call_spec.get("tool") or call_spec.get("name")
        args = call_spec.get("args") or call_spec.get("arguments") or {}

        if tool_name not in self._tools:
            result = {
                "ok": False,
                "data": None,
                "error": f"Tool '{tool_name}' not found in registry.",
                "elapsed_ms": 0.0
            }
            audit_logger.log_action(str(tool_name), args, result, 0.0)
            return result

        func = self._tools[tool_name]
        args, arg_err = _coerce_args(func, self._schemas[tool_name], args)
        if arg_err:
            result = {"ok": False, "data": None, "error": f"{tool_name}: {arg_err}", "elapsed_ms": 0.0}
            audit_logger.log_action(tool_name, args, result, 0.0)
            return result

        from seguro.safety.gate import safety_gate
        allowed, gate_reason = await safety_gate.gate({"tool": tool_name, "args": args}, state)
        if not allowed:
            result = {"ok": False, "data": None, "error": f"Blocked by safety gate: {gate_reason}", "elapsed_ms": 0.0}
            audit_logger.log_action(tool_name, args, result, 0.0)
            return result

        t0 = time.time()
        try:
            if inspect.iscoroutinefunction(func):
                res = await asyncio.wait_for(func(**args), timeout=timeout)
            else:
                # Run sync functions in thread pool to avoid blocking async loop
                def _thread_target():
                    try:
                        from seguro.tools.windows import ensure_default_desktop
                        ensure_default_desktop()
                    except Exception:
                        pass
                    return func(**args)

                res = await asyncio.wait_for(
                    asyncio.to_thread(_thread_target),
                    timeout=timeout
                )
            elapsed_ms = (time.time() - t0) * 1000.0
            ok, err = _derive_outcome(res)
            result = {
                "ok": ok,
                "data": res,
                "error": err,
                "elapsed_ms": round(elapsed_ms, 2)
            }
        except asyncio.TimeoutError:
            elapsed_ms = (time.time() - t0) * 1000.0
            result = {
                "ok": False,
                "data": None,
                "error": f"Tool '{tool_name}' timed out after {timeout}s.",
                "elapsed_ms": round(elapsed_ms, 2)
            }
        except Exception as e:
            elapsed_ms = (time.time() - t0) * 1000.0
            result = {
                "ok": False,
                "data": None,
                "error": str(e),
                "elapsed_ms": round(elapsed_ms, 2)
            }

        # Automatically record to audit log
        audit_logger.log_action(tool_name, args, result, result["elapsed_ms"])
        return result

# Global default tool registry
registry = ToolRegistry()
