import os
import re
import glob
import shutil
from seguro.tools.registry import registry
from seguro.safety.gate import safety_gate

def normalize_user_path(p: str) -> str:
    """Normalizes path and fixes model placeholder usernames like C:\\Users\\username\\..."""
    p = p.strip().strip("'\"")
    # Replace placeholder paths with real user home
    p = re.sub(
        r'^[A-Za-z]:[\\/]Users[\\/](?:username|user|your_username|your_name)[\\/]',
        lambda m: os.path.expanduser('~') + os.sep,
        p,
        flags=re.IGNORECASE
    )
    # If bare filename, default to Desktop
    if not os.path.isabs(p) and not p.startswith("~") and os.sep not in p and "/" not in p:
        p = os.path.join(os.path.expanduser("~/Desktop"), p)
    return os.path.abspath(os.path.expanduser(p))

@registry.register(
    name="files_read",
    description="Reads text content from a permitted file path.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or relative file path to read"},
            "max_lines": {"type": "integer", "description": "Maximum lines to read (default: 500)"}
        },
        "required": ["path"]
    }
)
def files_read(path: str, max_lines: int = 500) -> dict:
    real_path = normalize_user_path(path)
    ok, reason = safety_gate.check_path(real_path)
    if not ok:
        return {"ok": False, "error": reason}

    if not os.path.exists(real_path):
        return {"ok": False, "error": f"File '{real_path}' does not exist."}

    try:
        with open(real_path, "r", encoding="utf-8", errors="replace") as f:
            lines = [f.readline() for _ in range(max_lines)]
        return {"ok": True, "content": "".join(lines), "path": real_path}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

@registry.register(
    name="files_write",
    description="Writes text content to a permitted file path.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Target file path"},
            "content": {"type": "string", "description": "Content string to write"}
        },
        "required": ["path", "content"]
    }
)
def files_write(path: str, content: str) -> dict:
    real_path = normalize_user_path(path)
    ok, reason = safety_gate.check_path(real_path)
    if not ok:
        return {"ok": False, "error": reason}

    os.makedirs(os.path.dirname(real_path), exist_ok=True)
    note = None
    if real_path.lower().endswith(".json") and isinstance(content, str):
        import json, ast
        try:
            json.loads(content)
        except ValueError:
            try:  # Python-literal syntax ({'a': 1}) is a common slip; store real JSON instead
                content = json.dumps(ast.literal_eval(content), indent=2)
                note = "converted Python-literal content to JSON"
            except (ValueError, SyntaxError):
                pass  # written as given; the verifier will report it is not valid JSON
    try:
        with open(real_path, "w", encoding="utf-8") as f:
            f.write(content)
        out = {"ok": True, "path": real_path, "bytes_written": len(content.encode("utf-8"))}
        if note:
            out["normalized"] = note
        return out
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

@registry.register(
    name="files_move",
    description=(
        "Moves files. src is one path or a list of paths (globs like '*.txt' allowed). "
        "If dst is an existing folder, ends with '/' or src has several files, files go INTO "
        "the dst folder (created if needed); otherwise a single file is renamed to dst."
    ),
    parameters={
        "type": "object",
        "properties": {
            "src": {"type": "array", "items": {"type": "string"}, "description": "Source file path(s)"},
            "dst": {"type": "string", "description": "Destination folder, or new file path for a single rename"}
        },
        "required": ["src", "dst"]
    }
)
def files_move(src, dst: str) -> dict:
    srcs = src if isinstance(src, list) else [src]
    expanded = []
    for s in srcs:
        real = normalize_user_path(str(s))
        if any(ch in real for ch in "*?["):
            expanded.extend(sorted(p for p in glob.glob(real) if os.path.isfile(p)))
        else:
            expanded.append(real)
    if not expanded:
        return {"ok": False, "error": f"No source files matched {srcs}."}

    real_dst = normalize_user_path(dst)
    for p in expanded + [real_dst]:
        ok, reason = safety_gate.check_path(p)
        if not ok:
            return {"ok": False, "error": f"Path denied: {reason}"}
    missing = [p for p in expanded if not os.path.exists(p)]
    if missing:
        return {"ok": False, "error": f"Source(s) do not exist: {missing}"}

    into_dir = os.path.isdir(real_dst) or str(dst).endswith(("/", "\\")) or len(expanded) > 1
    moved = []
    try:
        if into_dir:
            os.makedirs(real_dst, exist_ok=True)
            for p in expanded:
                target = os.path.join(real_dst, os.path.basename(p))
                shutil.move(p, target)
                moved.append({"src": p, "dst": target})
        else:
            os.makedirs(os.path.dirname(real_dst), exist_ok=True)
            shutil.move(expanded[0], real_dst)
            moved.append({"src": expanded[0], "dst": real_dst})
        return {"ok": True, "moved": moved}
    except Exception as e:
        return {"ok": False, "error": str(e), "moved": moved}

@registry.register(
    name="files_list",
    description="Lists files in a folder with their sizes in bytes and KB (not recursive). Use it to filter files by size or type.",
    parameters={
        "type": "object",
        "properties": {
            "root": {"type": "string", "description": "Folder to list (e.g. '~/Desktop/reports')"}
        },
        "required": ["root"]
    }
)
def files_list(root: str) -> dict:
    real_root = normalize_user_path(root)
    ok, reason = safety_gate.check_path(real_root)
    if not ok:
        return {"ok": False, "error": reason}
    if not os.path.isdir(real_root):
        return {"ok": False, "error": f"Folder '{real_root}' does not exist."}
    entries = []
    for name in sorted(os.listdir(real_root)):
        p = os.path.join(real_root, name)
        if os.path.isfile(p):
            size = os.path.getsize(p)
            entries.append({"name": name, "path": p, "bytes": size, "kb": round(size / 1024, 1)})
        else:
            entries.append({"name": name, "path": p, "type": "folder"})
    return {"ok": True, "root": real_root, "count": len(entries), "entries": entries[:200]}

@registry.register(
    name="files_find",
    description="Finds files matching a glob pattern in permitted directories.",
    parameters={
        "type": "object",
        "properties": {
            "root": {"type": "string", "description": "Root folder to search in (e.g. '~/Desktop', '~/Documents')"},
            "pattern": {"type": "string", "description": "Glob pattern (e.g. '*.txt', '*.xlsx', '*quicksort*')"}
        },
        "required": ["pattern"]
    }
)
def files_find(pattern: str, root: str = "~/Desktop") -> dict:
    real_root = normalize_user_path(root)
    ok, reason = safety_gate.check_path(real_root)
    if not ok:
        return {"ok": False, "error": reason}

    search_path = os.path.join(real_root, "**", pattern)
    try:
        matches = glob.glob(search_path, recursive=True)
        return {"ok": True, "count": len(matches), "files": matches[:50]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

@registry.register(
    name="files_mkdir",
    description="Creates a directory or folder in a permitted user directory (e.g. ~/Desktop/FolderName).",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Folder path to create"}
        },
        "required": ["path"]
    }
)
def files_mkdir(path: str) -> dict:
    real_path = normalize_user_path(path)
    ok, reason = safety_gate.check_path(real_path)
    if not ok:
        return {"ok": False, "error": reason}

    try:
        os.makedirs(real_path, exist_ok=True)
        return {"ok": True, "path": real_path}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
