"""
Parameterised workflow templates. A verified workflow can be saved (variables proposed by the
LLM, confirmed by the user); later requests that match a template skip planning.
Placeholders are {{var:name}} (distinct from {{node.key}} blackboard refs).
"""
import json
import os
import re
from typing import Callable, Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, Field, ValidationError

from seguro.core.workflow.models import TaskGraph
from seguro.llm.client import LLMClient

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                             "templates")
VAR_RE = re.compile(r"\{\{var:([a-z][a-z0-9_]*)\}\}")


class Variable(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str
    value_in_this_run: str = Field(min_length=1)


class VariableProposal(BaseModel):
    template_name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,40}$")
    description: str
    variables: List[Variable] = Field(min_length=1, max_length=6)


class Template(BaseModel):
    name: str
    description: str
    example_request: str
    variables: Dict[str, str]          # name -> description
    graph: dict                        # TaskGraph JSON containing {{var:...}} placeholders
    runs: int = 0


class MatchResult(BaseModel):
    template: Optional[str] = None
    params: Dict[str, str] = Field(default_factory=dict)


def _dir(d: Optional[str]) -> str:
    return d or TEMPLATES_DIR


def load_all(d: Optional[str] = None) -> List[Template]:
    out = []
    if os.path.isdir(_dir(d)):
        for f in sorted(os.listdir(_dir(d))):
            if f.endswith(".yaml"):
                with open(os.path.join(_dir(d), f), encoding="utf-8") as fh:
                    out.append(Template.model_validate(yaml.safe_load(fh)))
    return out


def save(t: Template, d: Optional[str] = None) -> str:
    os.makedirs(_dir(d), exist_ok=True)
    path = os.path.join(_dir(d), f"{t.name}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(t.model_dump(), f, sort_keys=False, allow_unicode=True)
    return path


def instantiate(t: Template, params: Dict[str, str]) -> TaskGraph:
    missing = set(t.variables) - set(params)
    if missing:
        raise ValueError(f"missing template parameters: {sorted(missing)}")
    text = json.dumps(t.graph)

    def sub(m):
        return json.dumps(str(params[m.group(1)]))[1:-1]  # JSON-escape inside the string
    filled = VAR_RE.sub(sub, text)
    if VAR_RE.search(filled):
        raise ValueError("unfilled placeholders remain")
    return TaskGraph.model_validate(json.loads(filled))


def generalise(graph: TaskGraph, variables: List[Variable]) -> dict:
    """Replaces each variable's concrete value with its placeholder throughout the graph."""
    text = json.dumps(graph.model_dump())
    for v in sorted(variables, key=lambda v: -len(v.value_in_this_run)):
        needle = json.dumps(v.value_in_this_run)[1:-1]
        if needle not in text:
            raise ValueError(f"value {v.value_in_this_run!r} for '{v.name}' does not occur in the workflow")
        text = text.replace(needle, "{{var:%s}}" % v.name)
    return json.loads(text)


async def propose_and_save(run, confirm: Callable[[VariableProposal], bool], llm: Optional[LLMClient] = None,
                           d: Optional[str] = None) -> Optional[str]:
    """After a verified workflow: LLM proposes variables, user confirms, template is written."""
    if run.status != "done":
        return None
    llm = llm or LLMClient()
    messages = [{"role": "system", "content":
                 "You turn a finished automation into a reusable template. Pick the parts of the request a user "
                 "would change next time (topic, file names, counts, recipients). Each value_in_this_run must be "
                 "copied EXACTLY from the request text."},
                {"role": "user", "content": f"Request: {run.request}\nPlan: {json.dumps(run.graph.model_dump())[:4000]}"}]
    try:
        prop = VariableProposal.model_validate(json.loads(
            await llm.complete_json(messages, VariableProposal.model_json_schema(), name="variables")))
        graph_t = generalise(run.graph, prop.variables)
    except (ValidationError, ValueError) as e:
        print(f"[Templates] Could not derive a template: {e}")
        return None
    if not confirm(prop):
        return None
    example = run.request
    t = Template(name=prop.template_name, description=prop.description, example_request=example,
                 variables={v.name: v.description for v in prop.variables}, graph=graph_t)
    instantiate(t, {v.name: v.value_in_this_run for v in prop.variables})  # must round-trip
    return save(t, d)


async def match(request: str, llm: Optional[LLMClient] = None, d: Optional[str] = None) -> Optional[Tuple[Template, TaskGraph, dict]]:
    """Returns (template, filled graph, params) or None. Parameters must literally occur in the
    request, so a template can never run with values the user did not give."""
    templates = load_all(d)
    if not templates:
        return None
    llm = llm or LLMClient()
    catalog = [{"name": t.name, "description": t.description, "variables": t.variables,
                "example_request": t.example_request} for t in templates]
    messages = [{"role": "system", "content":
                 "Decide if the request is the same kind of task as one of these templates. If yes, return its "
                 "name and fill every variable with text copied exactly from the request. If no template fits "
                 "exactly, return template null.\n" + json.dumps(catalog)},
                {"role": "user", "content": request}]
    try:
        res = MatchResult.model_validate(json.loads(
            await llm.complete_json(messages, MatchResult.model_json_schema(), name="match")))
    except Exception as e:
        print(f"[Templates] match failed: {e}")
        return None
    t = next((t for t in templates if t.name == res.template), None)
    if t is None:
        return None
    params = {k: str(v).strip() for k, v in res.params.items() if k in t.variables}
    if set(params) != set(t.variables):
        return None
    if any(not v or v.lower() not in request.lower() for v in params.values()):
        return None
    try:
        return t, instantiate(t, params), params
    except (ValidationError, ValueError):
        return None
