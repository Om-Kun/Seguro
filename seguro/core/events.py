"""
Typed event bus. Every agent/workflow state change is emitted here; the GUI (Prompt 3)
and reports only consume these events. Emitting never blocks on subscribers' work.
"""
import json
import os
import threading
import time
import contextvars
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, List, Optional, Set

EVENT_TYPES: Set[str] = {
    "workflow_started", "plan_ready", "node_started", "tool_call", "node_verified",
    "node_failed", "replanned", "needs_confirmation", "confirmation_resolved", "workflow_done",
}

# Set by the executor around each node so tool_call events from the orchestrator are attributed.
current_context: contextvars.ContextVar = contextvars.ContextVar("seguro_event_context", default={})


@dataclass
class Event:
    type: str
    data: dict
    workflow_id: Optional[str] = None
    node_id: Optional[str] = None
    ts: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


class EventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._subs: Dict[int, tuple] = {}
        self._sinks: Dict[str, str] = {}
        self._next = 0

    def subscribe(self, callback: Callable[[Event], None], types: Optional[Set[str]] = None) -> int:
        """Callback runs on the emitting thread; it must be quick (GUI should only enqueue)."""
        with self._lock:
            self._next += 1
            self._subs[self._next] = (callback, set(types) if types else None)
            return self._next

    def unsubscribe(self, token: int):
        with self._lock:
            self._subs.pop(token, None)

    def add_sink(self, workflow_id: str, path: str):
        """Appends every event of this workflow to a JSONL file (source of truth for timings)."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with self._lock:
            self._sinks[workflow_id] = path

    def remove_sink(self, workflow_id: str):
        with self._lock:
            self._sinks.pop(workflow_id, None)

    def emit(self, type: str, workflow_id: Optional[str] = None, node_id: Optional[str] = None, **data) -> Event:
        if type not in EVENT_TYPES:
            raise ValueError(f"Unknown event type '{type}'")
        ctx = current_context.get() or {}
        ev = Event(type=type, data=data,
                   workflow_id=workflow_id or ctx.get("workflow_id"),
                   node_id=node_id or ctx.get("node_id"))
        with self._lock:
            subs = list(self._subs.values())
            sink = self._sinks.get(ev.workflow_id) if ev.workflow_id else None
            if sink:
                with open(sink, "a", encoding="utf-8") as f:
                    f.write(ev.to_json() + "\n")
        for cb, types in subs:
            if types is None or ev.type in types:
                try:
                    cb(ev)
                except Exception as e:  # a broken subscriber must never break the agent
                    print(f"[EventBus] subscriber error on {ev.type}: {e}")
        return ev


def read_events(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


bus = EventBus()
