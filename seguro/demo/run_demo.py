"""Runs the demo prompts through Seguro's orchestrator and prints a pass/fail table.
    python seguro/demo/run_demo.py                 # all
    python seguro/demo/run_demo.py --only 3,7      # some (1-based)
"""
import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from seguro.demo.setup_demo import PROMPTS
from seguro.core.orchestrator import orchestrator
from seguro.core.workflow.gates import broker
from seguro.skills.library.demo_skills import match_skill


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--approve-settings", action="store_true", help="pre-approve the dark-mode setting change")
    args = ap.parse_args()
    picks = [int(x) for x in args.only.split(",") if x.strip()] or list(range(1, len(PROMPTS) + 1))

    async def deny(reason, details):
        print(f"   (approval needed: {reason} -> denied in batch mode)")
        return False
    broker.configure(responder=deny, auto_approve={"skill_theme"} if args.approve_settings else set())

    results = []
    for i in picks:
        _category, title, prompt, needs = PROMPTS[i - 1]
        print(f"\n[{i:2d}] {title}\n     {prompt[:110]}")
        t0 = time.time()
        st = await orchestrator.run_task(prompt, mode="local_only")
        dt = time.time() - t0
        ok = st.status == "success"
        print(f"     {'✅ VERIFIED' if ok else '❌ FAILED'} in {dt:.1f}s  [{match_skill(prompt) or 'LLM agent'}]")
        print(f"     {(st.final_evidence or st.error_reason)[:300]}")
        results.append({"n": i, "title": title, "ok": ok, "seconds": round(dt, 1), "needs": needs,
                        "evidence": st.final_evidence or st.error_reason})
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_results.json")
    json.dump(results, open(out, "w", encoding="utf-8"), indent=2)
    print(f"\n{sum(r['ok'] for r in results)}/{len(results)} verified. Results: {out}")


if __name__ == "__main__":
    asyncio.run(main())
