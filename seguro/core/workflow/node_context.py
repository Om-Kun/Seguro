"""Context set by the executor while a node runs; tools read it (asyncio.to_thread copies context)."""
import contextvars

# {"expected": {key: kind}, "values": {key: value}} for the running node, else None.
current_node_outputs: contextvars.ContextVar = contextvars.ContextVar("seguro_node_outputs", default=None)

# time.time() when the running workflow started; files modified after it are its own artifacts.
workflow_started_at: contextvars.ContextVar = contextvars.ContextVar("seguro_workflow_started_at", default=None)


def is_own_artifact(path: str) -> bool:
    import os
    started = workflow_started_at.get()
    return bool(started) and os.path.exists(path) and os.path.getmtime(path) >= started - 1
