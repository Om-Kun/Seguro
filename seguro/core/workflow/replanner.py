"""
Revises only the failed subtree (failed node + its descendants) after a node exhausts its attempts.
Verified nodes are kept exactly as they are; the merged graph must validate.
"""
import json
from typing import List, Optional

from pydantic import BaseModel, Field, ValidationError

from seguro.core.workflow.models import Node, TaskGraph
from seguro.core.workflow.planner import CHECK_HELP, _tool_lines
from seguro.llm.client import LLMClient


class SubtreeRevision(BaseModel):
    model_config = {"extra": "forbid"}
    reason: str = Field(description="One sentence: why the new approach should work")
    nodes: List[Node] = Field(min_length=1, max_length=8)


def merge(graph: TaskGraph, replace_ids: List[str], new_nodes: List[Node]) -> TaskGraph:
    kept = [n for n in graph.nodes if n.id not in replace_ids]
    clash = {n.id for n in new_nodes} & {n.id for n in kept}
    if clash:
        raise ValueError(f"replacement nodes reuse ids of kept nodes: {sorted(clash)}")
    # A replan may change HOW, not lower the bar: every kind of check the replaced steps had
    # (e.g. docx_has_image) must still be required somewhere in the replacement.
    old_types = {c.type for n in graph.nodes if n.id in replace_ids for c in n.success_criteria
                 if c.type != "output_present"}
    new_types = {c.type for n in new_nodes for c in n.success_criteria}
    dropped = old_types - new_types
    if dropped:
        raise ValueError(f"replacement drops required checks {sorted(dropped)}; keep them (paths may change)")
    return TaskGraph.model_validate({"goal": graph.goal, "nodes": [n.model_dump() for n in kept + new_nodes]})


class Replanner:
    def __init__(self, llm: Optional[LLMClient] = None, escalation_llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient()
        self.escalation_llm = escalation_llm

    async def revise(self, run, failed_id: str) -> Optional[TaskGraph]:
        graph: TaskGraph = run.graph
        subtree = [failed_id] + graph.descendants(failed_id)
        kept = [n for n in graph.nodes if n.id not in subtree]
        st = run.states[failed_id]
        llm = self.escalation_llm if (run.mode == "hybrid" and self.escalation_llm) else self.llm
        prompt = (
            f"Workflow goal: {graph.goal}\n"
            f"Step '{failed_id}' failed after {st.attempts} attempt(s) using rung '{st.rung_used}'.\n"
            f"Error and failed checks:\n{(st.error or '')[:1500]}\n\n"
            f"Steps that already succeeded (keep them; you may reference their outputs):\n"
            + "\n".join(f"- {n.id}: {n.goal} outputs={dict(n.expected_output)}" for n in kept if
                        run.states[n.id].status == "verified")
            + f"\n\nAvailable data:\n{run.bb.summary()}\n\n"
            f"Steps to replace (the failed one and everything after it):\n"
            + json.dumps([graph.node(i).model_dump() for i in subtree])[:4000]
            + "\n\nReturn replacement nodes that reach the same end result a DIFFERENT way "
              "(e.g. a file/API tool instead of GUI, a smaller step, a different tool). "
              "Use NEW ids (suffix _v2). Later steps must depend on the new ids."
        )
        messages = [{"role": "system", "content": "You repair failed steps of a desktop-automation plan.\n"
                                                  + CHECK_HELP + "\nTools:\n" + _tool_lines()},
                    {"role": "user", "content": prompt}]
        for _ in range(2):
            try:
                raw = await llm.complete_json(messages, SubtreeRevision.model_json_schema(), name="revision",
                                              max_tokens=3000)
            except Exception as e:
                print(f"[Replanner] LLM error: {e}")
                return None
            try:
                rev = SubtreeRevision.model_validate(json.loads(raw))
                return merge(graph, subtree, rev.nodes)
            except (ValidationError, ValueError) as e:
                messages += [{"role": "assistant", "content": raw[:3000]},
                             {"role": "user", "content": f"Invalid: {str(e)[:1200]}\nReturn corrected JSON."}]
        return None
