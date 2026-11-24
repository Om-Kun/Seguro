import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

@dataclass
class StepRecord:
    step_num: int
    tool: str
    args: dict
    result: dict
    verifier_check: Optional[dict] = None
    timestamp: float = field(default_factory=time.time)

@dataclass
class TaskState:
    request: str
    mode: str = "hybrid"   # "local_only" | "hybrid"
    max_steps: int = 25
    step_count: int = 0
    consecutive_failures: int = 0
    history: List[StepRecord] = field(default_factory=list)
    status: str = "running"   # "running", "success", "failed", "aborted"
    final_evidence: str = ""
    error_reason: str = ""

    def record_step(self, tool: str, args: dict, result: dict, check: dict = None):
        self.step_count += 1
        rec = StepRecord(
            step_num=self.step_count,
            tool=tool,
            args=args,
            result=result,
            verifier_check=check
        )
        self.history.append(rec)
        if not result.get("ok"):
            self.consecutive_failures += 1
        else:
            self.consecutive_failures = 0

    def abort(self, reason: str = "killswitch"):
        self.status = "aborted"
        self.error_reason = reason
        return {"status": "aborted", "reason": reason, "steps": self.step_count}

    def success(self, evidence: str = ""):
        self.status = "success"
        self.final_evidence = evidence
        return {"status": "success", "evidence": evidence, "steps": self.step_count}

    def give_up(self, reason: str = "step budget exhausted"):
        self.status = "failed"
        self.error_reason = reason
        return {"status": "failed", "reason": reason, "steps": self.step_count}
