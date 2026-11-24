import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from seguro.core.workflow import templates
from seguro.core.workflow.models import TaskGraph


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    async def complete_json(self, messages, schema, name="x", **kw):
        return self.replies.pop(0)


class FakeRun:
    status = "done"
    request = "Find 5 latest news about robotics and save robotics_news.xlsx on the Desktop"
    graph = TaskGraph.model_validate({"goal": request, "nodes": [
        {"id": "news", "goal": "Search 5 latest news about robotics", "expected_output": {"table": "table"}},
        {"id": "sheet", "goal": "Save the table to robotics_news.xlsx on the Desktop",
         "inputs": {"t": "{{news.table}}"}, "expected_output": {"file_path": "path"},
         "success_criteria": [{"type": "xlsx_min_rows", "path": "{{sheet.file_path}}", "min_rows": 5}]}]})


PROPOSAL = json.dumps({"template_name": "news_to_excel", "description": "Latest news on a topic into Excel",
                       "variables": [{"name": "topic", "description": "news topic", "value_in_this_run": "robotics"}]})


@pytest.mark.asyncio
async def test_save_then_match_roundtrip(tmp_path):
    path = await templates.propose_and_save(FakeRun(), lambda p: True, FakeLLM(PROPOSAL), d=str(tmp_path))
    assert path and os.path.exists(path)
    t = templates.load_all(str(tmp_path))[0]
    assert "{{var:topic}}" in json.dumps(t.graph) and "robotics" not in json.dumps(t.graph)

    req = "Find 5 latest news about space and save space_news.xlsx on the Desktop"
    hit = await templates.match(req, FakeLLM(json.dumps({"template": "news_to_excel", "params": {"topic": "space"}})),
                                d=str(tmp_path))
    assert hit is not None
    _, graph, params = hit
    assert params == {"topic": "space"} and graph.node("sheet").goal.endswith("space_news.xlsx on the Desktop")
    # node refs survive templating
    assert graph.node("sheet").inputs["t"] == "{{news.table}}"


@pytest.mark.asyncio
async def test_match_rejects_parameters_not_in_request(tmp_path):
    await templates.propose_and_save(FakeRun(), lambda p: True, FakeLLM(PROPOSAL), d=str(tmp_path))
    hallucinated = json.dumps({"template": "news_to_excel", "params": {"topic": "quantum"}})
    assert await templates.match("Find 5 latest news about space", FakeLLM(hallucinated), d=str(tmp_path)) is None
    assert await templates.match("x", FakeLLM(json.dumps({"template": None, "params": {}})), d=str(tmp_path)) is None


@pytest.mark.asyncio
async def test_user_can_decline_and_bad_values_are_rejected(tmp_path):
    assert await templates.propose_and_save(FakeRun(), lambda p: False, FakeLLM(PROPOSAL), d=str(tmp_path)) is None
    bad = json.dumps({"template_name": "x_bad", "description": "d",
                      "variables": [{"name": "topic", "description": "t", "value_in_this_run": "not in plan"}]})
    assert await templates.propose_and_save(FakeRun(), lambda p: True, FakeLLM(bad), d=str(tmp_path)) is None
    assert templates.load_all(str(tmp_path)) == []
