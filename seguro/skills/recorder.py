import os
import re
import json
import time
from typing import List, Dict, Any, Optional

CANDIDATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library", "candidates")

class SkillRecorder:
    """
    Trajectory Recorder for Seguro V2.
    When a complex task completes with verified physical evidence, saves the
    exact trajectory of tool calls and parameters to skills/library/candidates/.
    Supports promoting successful trajectories into instant reusable skills.
    """

    def __init__(self, candidates_dir: str = CANDIDATES_DIR):
        self.candidates_dir = candidates_dir
        os.makedirs(self.candidates_dir, exist_ok=True)

    def record_successful_task(self, prompt: str, steps: list, final_evidence: str) -> Optional[str]:
        """
        Saves verified trajectory as a candidate skill.
        """
        if not steps:
            return None

        slug = re.sub(r'[^a-zA-Z0-9_\-]+', '_', prompt.lower().strip())[:40].strip('_') or "skill"
        candidate_id = f"{slug}_{int(time.time())}"
        filepath = os.path.join(self.candidates_dir, f"{candidate_id}.json")

        trajectory = []
        for s in steps:
            trajectory.append({
                "step": getattr(s, "step_num", 0),
                "tool": getattr(s, "tool", ""),
                "args": getattr(s, "args", {}),
                "verified": getattr(getattr(s, "verifier_check", None), "get", lambda k: False)("verified") if getattr(s, "verifier_check", None) else True
            })

        manifest = {
            "id": candidate_id,
            "original_prompt": prompt,
            "recorded_at": time.time(),
            "final_evidence": final_evidence,
            "step_count": len(trajectory),
            "trajectory": trajectory
        }

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
            print(f"[SkillRecorder] Saved candidate skill trajectory to {filepath}")
            return filepath
        except Exception as e:
            print(f"[SkillRecorder] Failed to save candidate skill: {e}")
            return None

skill_recorder = SkillRecorder()
