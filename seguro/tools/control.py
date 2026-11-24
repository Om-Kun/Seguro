from seguro.tools.registry import registry

@registry.register(
    name="task_complete",
    description="Signals that the user's task is fully finished with concrete verifiable evidence (e.g. file path, window state, calculation output).",
    parameters={
        "type": "object",
        "properties": {
            "evidence": {"type": "string", "description": "Concrete evidence demonstrating task completion (file paths, values, states)"}
        },
        "required": ["evidence"]
    }
)
def task_complete(evidence: str) -> dict:
    return {"status": "complete", "evidence": evidence}

@registry.register(
    name="task_failed",
    description="Signals that the task cannot be completed, providing an honest technical reason.",
    parameters={
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "Technical reason why the task could not be completed"}
        },
        "required": ["reason"]
    }
)
def task_failed(reason: str) -> dict:
    return {"status": "failed", "reason": reason}

@registry.register(
    name="ask_user",
    description="Asks the user a clarifying question when intent is ambiguous or critical input is missing.",
    parameters={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "Question to ask the user"}
        },
        "required": ["question"]
    }
)
def ask_user(question: str) -> dict:
    print(f"\n[Seguro asks user]: {question}")
    return {"status": "asked", "question": question}


@registry.register(
    name="node_output",
    description="Inside a workflow step: publishes one required output (e.g. key='file_path', value='C:/.../report.xlsx'). "
                "Call it once per required output before task_complete.",
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Output name listed in 'Required outputs'"},
            "value": {"type": "string", "description": "The value: a path, text, URL, number, or a table as JSON text of a list of rows"}
        },
        "required": ["key", "value"]
    }
)
def node_output(key: str, value) -> dict:
    from seguro.core.workflow.node_context import current_node_outputs
    from seguro.core.workflow.blackboard import _validate, OutputTypeError
    sink = current_node_outputs.get()
    if sink is None:
        return {"ok": False, "error": "node_output can only be used inside a workflow step."}
    expected = sink["expected"]
    if key not in expected:
        return {"ok": False, "error": f"'{key}' is not a required output. Required: {sorted(expected)}"}
    try:
        if expected[key] == "path":
            from seguro.tools.files import normalize_user_path
            value = normalize_user_path(str(value))
        value = _validate(expected[key], value)
    except (OutputTypeError, ValueError, TypeError) as e:
        return {"ok": False, "error": f"Invalid value for '{key}' ({expected[key]}): {e}"}
    sink["values"][key] = value
    missing = sorted(set(expected) - set(sink["values"]))
    return {"ok": True, "key": key, "kind": expected[key], "still_missing": missing}
