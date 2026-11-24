"""Typed store of node outputs. Large values live in files under the workflow workdir."""
import json
import os
from typing import Any, Dict

from seguro.core.workflow.models import REF_RE

INLINE_LIMIT = 4096


class UnresolvedRef(KeyError):
    pass


class OutputTypeError(ValueError):
    pass


def validate_file_content(p: str):
    """A file must actually be what its extension says: a 9-byte text file named .png is not a chart."""
    ext = os.path.splitext(p)[1].lower()
    try:
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp"):
            from PIL import Image
            with Image.open(p) as im:
                im.verify()
        elif ext == ".xlsx":
            import openpyxl
            openpyxl.load_workbook(p, read_only=True).close()
        elif ext == ".docx":
            from docx import Document
            Document(p)
        elif ext == ".pptx":
            from pptx import Presentation
            Presentation(p)
        elif ext == ".json":
            with open(p, encoding="utf-8") as f:
                json.load(f)
    except Exception as e:
        raise OutputTypeError(f"'{p}' is not a valid {ext} file ({type(e).__name__}: {e})")
    if os.path.isfile(p) and os.path.getsize(p) == 0:
        raise OutputTypeError(f"'{p}' is empty")


def _validate(kind: str, value: Any) -> Any:
    if kind == "path":
        p = os.path.abspath(os.path.expanduser(str(value)))
        if not os.path.exists(p):
            raise OutputTypeError(f"path output '{p}' does not exist")
        validate_file_content(p)
        return p
    if kind == "url":
        if not str(value).startswith(("http://", "https://", "file:///")):
            raise OutputTypeError(f"url output '{value}' is not a URL")
        return str(value)
    if kind == "table":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                import ast
                value = ast.literal_eval(value)  # models often send Python-style [['a', 1]]
        if not isinstance(value, list) or not value:
            raise OutputTypeError("table output must be a non-empty list of rows")
        return value
    if kind == "number":
        return float(value)
    if kind == "json":
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except ValueError:
            import ast
            return ast.literal_eval(value)
    text = str(value)
    if not text.strip():
        raise OutputTypeError("text output is empty")
    return text


class Blackboard:
    def __init__(self, workdir: str):
        self.workdir = workdir
        self.entries: Dict[str, Dict[str, dict]] = {}

    def put(self, node_id: str, key: str, value: Any, kind: str = "text") -> Any:
        value = _validate(kind, value)
        blob = json.dumps(value, default=str)
        entry = {"kind": kind}
        if len(blob) > INLINE_LIMIT:
            data_dir = os.path.join(self.workdir, "data")
            os.makedirs(data_dir, exist_ok=True)
            ref = os.path.join(data_dir, f"{node_id}__{key}.json")
            with open(ref, "w", encoding="utf-8") as f:
                f.write(blob)
            entry["ref"] = ref
        else:
            entry["value"] = value
        self.entries.setdefault(node_id, {})[key] = entry
        return value

    def has(self, node_id: str, key: str) -> bool:
        return key in self.entries.get(node_id, {})

    def get(self, node_id: str, key: str) -> Any:
        try:
            entry = self.entries[node_id][key]
        except KeyError:
            raise UnresolvedRef(f"{{{{{node_id}.{key}}}}} has no value yet")
        if "ref" in entry:
            with open(entry["ref"], encoding="utf-8") as f:
                return json.loads(f.read())
        return entry["value"]

    def resolve(self, template: str) -> Any:
        """A string that is exactly one ref returns the raw value (lists stay lists); otherwise
        refs are substituted as text. Unknown refs raise UnresolvedRef, never resolve to ''."""
        if not isinstance(template, str):
            return template
        whole = REF_RE.fullmatch(template.strip())
        if whole:
            return self.get(whole.group(1), whole.group(2))

        def sub(m):
            v = self.get(m.group(1), m.group(2))
            return v if isinstance(v, str) else json.dumps(v, default=str)
        return REF_RE.sub(sub, template)

    def resolve_obj(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: self.resolve_obj(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.resolve_obj(v) for v in obj]
        return self.resolve(obj)

    def summary(self, max_chars: int = 1500) -> str:
        """Compact description for prompts (values truncated)."""
        lines = []
        for node_id, keys in self.entries.items():
            for key, e in keys.items():
                v = f"<file {e['ref']}>" if "ref" in e else json.dumps(e["value"], default=str)
                lines.append(f"{node_id}.{key} ({e['kind']}): {v[:200]}")
        text = "\n".join(lines)
        return text[:max_chars]

    def to_dict(self) -> dict:
        return {"workdir": self.workdir, "entries": self.entries}

    @classmethod
    def from_dict(cls, d: dict) -> "Blackboard":
        bb = cls(d["workdir"])
        bb.entries = d.get("entries", {})
        return bb
