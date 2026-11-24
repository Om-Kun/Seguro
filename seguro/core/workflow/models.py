"""Pydantic schema for TaskGraph plans. Success criteria are typed checks, never code."""
import re
from typing import Annotated, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator

REF_RE = re.compile(r"\{\{\s*([a-z0-9_]+)\.([a-zA-Z0-9_]+)\s*\}\}")

Rung = Literal["skill", "shell", "api", "uia", "vision", "delegate", "cloud", "any"]
OutputKind = Literal["path", "text", "table", "url", "json", "number"]


# ---- typed success checks (paths/text may contain {{node.key}} refs) -------------------------
class _Check(BaseModel):
    model_config = {"extra": "forbid"}

class FileExists(_Check):
    type: Literal["file_exists"]
    path: str

class FileContains(_Check):
    type: Literal["file_contains"]
    path: str
    text: str

class XlsxMinRows(_Check):
    type: Literal["xlsx_min_rows"]
    path: str
    min_rows: int = Field(ge=1)
    sheet: Optional[str] = None

class XlsxNumericColumn(_Check):
    type: Literal["xlsx_numeric_column"]
    path: str
    column: int = Field(ge=1, description="1-based column index")
    min_count: int = Field(default=1, ge=1)
    sheet: Optional[str] = None

class XlsxHasChart(_Check):
    type: Literal["xlsx_has_chart"]
    path: str
    sheet: Optional[str] = None

class XlsxHasColumns(_Check):
    type: Literal["xlsx_has_columns"]
    path: str
    columns: List[str] = Field(min_length=1)
    sheet: Optional[str] = None

class XlsxHasSheets(_Check):
    type: Literal["xlsx_has_sheets"]
    path: str
    sheets: List[str] = Field(min_length=1)

class DocxContains(_Check):
    type: Literal["docx_contains"]
    path: str
    text: str

class DocxHasImage(_Check):
    type: Literal["docx_has_image"]
    path: str
    min_images: int = Field(default=1, ge=1)

class PptxSlideCount(_Check):
    type: Literal["pptx_slide_count"]
    path: str
    min: int = Field(default=1, ge=1)
    max: Optional[int] = None

class PptxHasImage(_Check):
    type: Literal["pptx_has_image"]
    path: str

class FolderHasFiles(_Check):
    type: Literal["folder_has_files"]
    path: str
    pattern: str = "*"
    min_count: int = Field(default=1, ge=1)

class WindowTitleContains(_Check):
    type: Literal["window_title_contains"]
    text: str

class HttpOk(_Check):
    type: Literal["http_ok"]
    url: str
    contains: Optional[str] = None

class OutputPresent(_Check):
    type: Literal["output_present"]
    key: str

class GmailDraftExists(_Check):
    type: Literal["gmail_draft_exists"]
    subject_contains: str
    with_attachment: bool = True

CHECK_CLASSES = (
    FileExists, FileContains, XlsxMinRows, XlsxNumericColumn, XlsxHasChart,
    XlsxHasColumns, XlsxHasSheets, DocxContains, DocxHasImage,
    PptxSlideCount, PptxHasImage, FolderHasFiles, WindowTitleContains, HttpOk, OutputPresent, GmailDraftExists,
)
Check = Annotated[Union[CHECK_CLASSES], Field(discriminator="type")]
CHECK_TYPES = [c.model_fields["type"].annotation.__args__[0] for c in CHECK_CLASSES]


# ---- graph -------------------------------------------------------------------------------------
class Node(BaseModel):
    model_config = {"extra": "forbid"}

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    goal: str = Field(min_length=5)
    preferred_rung: Rung = "any"
    depends_on: List[str] = Field(default_factory=list)
    inputs: Dict[str, str] = Field(default_factory=dict, description="name -> literal or {{node.key}}")
    expected_output: Dict[str, OutputKind] = Field(default_factory=dict)
    success_criteria: List[Check] = Field(default_factory=list)
    max_attempts: int = Field(default=2, ge=1, le=5)
    requires_confirmation: bool = False
    gui: bool = False

    def refs(self) -> List[tuple]:
        """All {{node.key}} references in inputs and check fields."""
        found = []
        texts = list(self.inputs.values())
        for c in self.success_criteria:
            texts.extend(str(v) for v in c.model_dump().values())
        for t in texts:
            found.extend(REF_RE.findall(t))
        return found

    @model_validator(mode="after")
    def _ensure_checks(self):
        # Every declared output gets at least a presence check; a node with no outputs and
        # no checks can never be verified, which the graph validator reports.
        present = {c.key for c in self.success_criteria if isinstance(c, OutputPresent)}
        for key in self.expected_output:
            if key not in present:
                self.success_criteria.append(OutputPresent(type="output_present", key=key))
        return self


class TaskGraph(BaseModel):
    model_config = {"extra": "forbid"}

    goal: str
    nodes: List[Node] = Field(min_length=1, max_length=12)
    fallback: bool = Field(default=False, description="Set only by the system for single-step fallback plans")

    @field_validator("nodes")
    @classmethod
    def _unique_ids(cls, nodes):
        ids = [n.id for n in nodes]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"duplicate node ids: {sorted(dup)}")
        return nodes

    @model_validator(mode="after")
    def _validate_graph(self):
        by_id = {n.id: n for n in self.nodes}
        errors = []
        for n in self.nodes:
            for d in n.depends_on:
                if d not in by_id:
                    errors.append(f"{n.id}.depends_on references unknown node '{d}'")
                elif d == n.id:
                    errors.append(f"{n.id} depends on itself")
            for ref_node, ref_key in n.refs():
                if ref_node == n.id:
                    continue  # a check may refer to the node's own output
                if ref_node not in by_id:
                    errors.append(f"{n.id} references unknown node '{ref_node}'")
                elif ref_key not in by_id[ref_node].expected_output:
                    errors.append(f"{n.id} references '{ref_node}.{ref_key}' but {ref_node} declares outputs "
                                  f"{sorted(by_id[ref_node].expected_output)}")
                elif ref_node not in n.depends_on:
                    n.depends_on.append(ref_node)  # refs imply a dependency
            if not n.success_criteria and not self.fallback:
                errors.append(f"{n.id} has no expected_output and no success_criteria, so it can never be verified")
        if errors:
            raise ValueError("; ".join(errors))
        self.topo_order()  # raises on cycles
        return self

    def node(self, node_id: str) -> Node:
        return next(n for n in self.nodes if n.id == node_id)

    def topo_order(self) -> List[str]:
        deps = {n.id: set(n.depends_on) for n in self.nodes}
        order, ready = [], sorted(i for i, d in deps.items() if not d)
        while ready:
            cur = ready.pop(0)
            order.append(cur)
            for i, d in deps.items():
                if cur in d:
                    d.discard(cur)
                    if not d and i not in order and i not in ready:
                        ready.append(i)
        if len(order) != len(self.nodes):
            raise ValueError(f"dependency cycle among nodes {sorted(set(deps) - set(order))}")
        return order

    def descendants(self, node_id: str) -> List[str]:
        out, frontier = [], [node_id]
        while frontier:
            cur = frontier.pop()
            for n in self.nodes:
                if cur in n.depends_on and n.id not in out:
                    out.append(n.id)
                    frontier.append(n.id)
        return out


NodeStatus = Literal["pending", "running", "verified", "failed", "skipped", "interrupted", "waiting_confirmation"]


class NodeState(BaseModel):
    status: NodeStatus = "pending"
    attempts: int = 0
    error: Optional[str] = None
    check_results: List[dict] = Field(default_factory=list)
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    rung_used: Optional[str] = None
    tool_calls: int = 0
    escalated: bool = False
