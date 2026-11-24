import os
import json
import time
from datetime import datetime
import threading

class AuditLogger:
    def __init__(self, log_dir: str = None):
        if log_dir is None:
            # Default to logs/ in seguro parent directory
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            log_dir = os.path.join(base_dir, "logs")
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self._lock = threading.Lock()

    def _get_log_path(self) -> str:
        date_str = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"{date_str}.jsonl")

    def log(self, event_type: str, data: dict):
        entry = {
            "timestamp": time.time(),
            "datetime": datetime.now().isoformat(),
            "event_type": event_type,
            **data
        }
        line = json.dumps(entry, default=str)
        with self._lock:
            with open(self._get_log_path(), "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def log_action(self, tool_name: str, args: dict, result: dict, elapsed_ms: float, task_id: str = "default"):
        self.log("tool_action", {
            "task_id": task_id,
            "tool": tool_name,
            "args": args,
            "result": result,
            "elapsed_ms": elapsed_ms
        })

# Global default audit logger
audit_logger = AuditLogger()
