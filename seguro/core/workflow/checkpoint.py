"""Persists graph + node states + blackboard to workflows/<id>/state.json after every change."""
import json
import os
import time
from typing import List, Optional

WORKFLOWS_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "workflows")


def workflow_dir(workflow_id: str, root: Optional[str] = None) -> str:
    return os.path.join(root or WORKFLOWS_ROOT, workflow_id)


def save(workflow_id: str, payload: dict, root: Optional[str] = None) -> str:
    d = workflow_dir(workflow_id, root)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "state.json")
    tmp = path + ".tmp"
    payload = dict(payload, saved_at=time.time())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # atomic on the same volume: a kill never leaves a half-written state
    return path


def load(workflow_id: str, root: Optional[str] = None) -> dict:
    path = os.path.join(workflow_dir(workflow_id, root), "state.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"No checkpoint for workflow '{workflow_id}' at {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_workflows(root: Optional[str] = None) -> List[dict]:
    root = root or WORKFLOWS_ROOT
    out = []
    if not os.path.isdir(root):
        return out
    for wid in sorted(os.listdir(root)):
        try:
            st = load(wid, root)
            out.append({"id": wid, "status": st.get("status"), "goal": st.get("graph", {}).get("goal"),
                        "saved_at": st.get("saved_at")})
        except Exception:
            continue
    return out
