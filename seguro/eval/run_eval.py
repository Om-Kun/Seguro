import os
import sys
import time
import json
import yaml
import asyncio
import argparse
from typing import Dict, Any, List

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add root folder to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from seguro.core.orchestrator import orchestrator
from seguro.tools.windows import windows_list, windows_close
from seguro.safety.gate import safety_gate
from seguro.safety.killswitch import killswitch
from seguro.core.workflow.gates import broker


async def _deny_all(reason, details):
    return False

TASKS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks.yaml")
WORKFLOW_TASKS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflow_tasks.yaml")
HARD_TASKS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hard_tasks.yaml")
HARD_TASKS_2_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hard_tasks_2.yaml")


def _camera_available() -> bool:
    import cv2
    for backend in (cv2.CAP_MSMF, cv2.CAP_DSHOW):
        cap = cv2.VideoCapture(0, backend)
        ok = cap.isOpened() and cap.read()[0]
        cap.release()
        if ok:
            return True
    return False
RESULTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.json")
REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report.md")

async def run_single_eval_task(task: dict, mode: str = "local_only") -> dict:
    task_id = task.get("id")
    category = task.get("category", "general")
    prompt = task.get("prompt")
    setup_code = task.get("setup")
    check_code = task.get("check")
    cleanup_code = task.get("cleanup")
    timeout_s = task.get("timeout_s", 30)

    if task.get("requires") == "camera" and not _camera_available():
        return {"id": task_id, "category": category, "prompt": prompt, "status": "SKIP",
                "error": "No camera connected", "elapsed_s": 0.0, "steps": 0, "rungs": [], "agent_status": None}

    # 1. Run Setup (its globals stay visible to workflow checkers, e.g. random nonces)
    setup_globals = {"os": os, "windows_list": windows_list, "windows_close": windows_close}
    if setup_code:
        try:
            exec(setup_code, setup_globals)
        except Exception as e:
            print(f"[{task_id}] Setup error: {e}")

    # Gated actions: only what the task explicitly pre-approves; everything else is denied.
    broker.configure(responder=_deny_all, auto_approve=set(task.get("approve", [])))

    if task.get("workflow"):
        import re
        from seguro.eval.workflow_eval import run_workflow_task
        task = dict(task, prompt=re.sub(r"\{([A-Z][A-Z0-9_]*)\}",
                                        lambda m: str(setup_globals.get(m.group(1), m.group(0))), prompt))
        prompt = task["prompt"]
        res = await run_workflow_task(task, mode, setup_globals)
        if cleanup_code:
            try:
                exec(cleanup_code, setup_globals)
            except Exception:
                pass
        return {"id": task_id, "category": category, "prompt": prompt, **res}

    # 2. Run Orchestrator Task
    t0 = time.time()
    try:
        state = await asyncio.wait_for(
            orchestrator.run_task(prompt, mode=mode),
            timeout=timeout_s
        )
        elapsed_s = time.time() - t0
    except asyncio.TimeoutError:
        elapsed_s = time.time() - t0
        return {
            "id": task_id,
            "category": category,
            "prompt": prompt,
            "status": "FAIL",
            "error": f"Timed out after {timeout_s}s",
            "elapsed_s": round(elapsed_s, 2),
            "steps": 0,
            "rungs": []
        }
    except Exception as e:
        elapsed_s = time.time() - t0
        return {
            "id": task_id,
            "category": category,
            "prompt": prompt,
            "status": "FAIL",
            "error": str(e),
            "elapsed_s": round(elapsed_s, 2),
            "steps": 0,
            "rungs": []
        }

    # 3. Run Check Verification
    passed = False
    check_error = None
    if check_code:
        try:
            eval_globals = {
                "os": os,
                "time": time,
                "json": json,
                "state": state,
                "windows_list": windows_list,
                "windows_close": windows_close
            }
            exec(check_code, eval_globals)
            passed = True
        except AssertionError as ae:
            passed = False
            check_error = f"Assertion failed: {ae}" if str(ae) else f"Assertion failed: {check_code.strip()[:200]}"
        except Exception as e:
            passed = False
            check_error = str(e)
    else:
        passed = (state.status == "success")

    # 4. Run Cleanup
    if cleanup_code:
        try:
            exec(cleanup_code, {"os": os, "windows_list": windows_list, "windows_close": windows_close})
        except Exception:
            pass

    rungs_used = [getattr(s, "tool", "") for s in state.history]

    return {
        "id": task_id,
        "category": category,
        "prompt": prompt,
        "status": "PASS" if passed else "FAIL",
        "agent_status": state.status,
        "check_is_self_report": bool(check_code) and "state.status" in check_code and "os.path" not in check_code and "windows_list" not in check_code,
        "evidence": state.final_evidence or state.error_reason,
        "error": check_error,
        "elapsed_s": round(elapsed_s, 2),
        "steps": state.step_count,
        "rungs": rungs_used
    }

def generate_markdown_report(results: List[dict], mode: str) -> str:
    total = len(results)
    if total == 0:
        return "# Seguro Evaluation Report\n\nNo tasks evaluated."

    passed_count = sum(1 for r in results if r["status"] == "PASS")
    pass_pct = (passed_count / total) * 100.0

    times = [r["elapsed_s"] for r in results]
    times.sort()
    med_time = times[len(times) // 2] if times else 0.0
    p90_idx = int(0.9 * len(times)) - 1 if times else 0
    p90_time = times[max(0, p90_idx)] if times else 0.0

    # Category breakdown
    cats = {}
    for r in results:
        c = r["category"]
        if c not in cats:
            cats[c] = {"total": 0, "passed": 0, "times": []}
        cats[c]["total"] += 1
        if r["status"] == "PASS":
            cats[c]["passed"] += 1
        cats[c]["times"].append(r["elapsed_s"])

    lines = [
        "# 🛡️ SEGURO V2 EMPIRICAL BENCHMARK EVALUATION REPORT",
        "",
        f"**Evaluation Mode:** `{mode.upper()}`  ",
        f"**Hardware Platform:** NVIDIA GeForce RTX 3060 12GB | Windows 11 Workstation  ",
        f"**Date / Timestamp:** `{time.strftime('%Y-%m-%d %H:%M:%S')}`  ",
        f"**Philosophy Enforced:** *Tools first. Pixels last. Cloud when stuck. Never fake an output.*",
        "",
        "---",
        "",
        "## 1. Executive Summary & Core KPIs",
        "",
        f"| Metric | Measured Value | Spec Target |",
        f"|---|---|---|",
        f"| **Overall Accuracy** | **{pass_pct:.1f}%** ({passed_count}/{total}) | ≥ 70% Local / ≥ 90% Hybrid |",
        f"| **Median Latency** | **{med_time:.2f}s** | < 2.50s |",
        f"| **P90 Latency** | **{p90_time:.2f}s** | < 8.00s |",
        f"| **False success claims** | **{sum(1 for r in results if r['status'] != 'PASS' and r.get('agent_status') == 'success')}** | 0 |",
        "",
        "---",
        "",
        "## 2. Category Performance Breakdown",
        "",
        "| Category | Tasks | Passed | Accuracy (%) | Median Latency (s) |",
        "|---|---|---|---|---|"
    ]

    for c, stat in sorted(cats.items()):
        c_tot = stat["total"]
        c_pass = stat["passed"]
        c_pct = (c_pass / c_tot) * 100.0 if c_tot else 0.0
        c_times = sorted(stat["times"])
        c_med = c_times[len(c_times) // 2] if c_times else 0.0
        lines.append(f"| `{c}` | {c_tot} | {c_pass} | **{c_pct:.1f}%** | {c_med:.2f}s |")

    lines.extend([
        "",
        "---",
        "",
        "## 3. Detailed Per-Task Execution Ledger",
        "",
        "| Task ID | Category | Status | Latency | Steps | Output / Evidence |",
        "|---|---|---|---|---|---|"
    ])

    for r in results:
        status_icon = "✅ PASS" if r["status"] == "PASS" else "❌ FAIL"
        ev = (r.get("evidence") or r.get("error") or "").replace("|", "\\|")[:60]
        lines.append(f"| `{r['id']}` | `{r['category']}` | {status_icon} | {r['elapsed_s']:.2f}s | {r['steps']} | {ev} |")

    tools_used = sorted({t for r in results for t in r.get("rungs", [])})
    weak = sum(1 for r in results if r.get("check_is_self_report"))
    lines.extend([
        "",
        "---",
        "",
        "## 4. Notes",
        "",
        f"- Tools actually called in this run: {', '.join(f'`{t}`' for t in tools_used) or 'none'}.",
        f"- Runs whose checker only reads the agent's own status (not independent evidence): {weak}.",
        "- Tool calls are logged to `seguro/logs/YYYY-MM-DD.jsonl`."
    ])

    return "\n".join(lines)

async def main_eval():
    parser = argparse.ArgumentParser(description="Seguro V2 Evaluation Runner")
    parser.add_argument("--mode", choices=["local_only", "hybrid"], default="local_only", help="Evaluation mode")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of tasks to run")
    parser.add_argument("--category", type=str, default=None, help="Filter by category")
    parser.add_argument("--tasks", type=str, default="all", help="Comma-separated task ids, or 'all'")
    parser.add_argument("--repeat", type=int, default=1, help="Runs per task")
    parser.add_argument("--out", type=str, default=None, help="Results JSON path (default: eval/results.json)")
    args = parser.parse_args()

    tasks = []
    for path in (TASKS_PATH, WORKFLOW_TASKS_PATH, HARD_TASKS_PATH, HARD_TASKS_2_PATH):
        with open(path, "r", encoding="utf-8") as f:
            tasks.extend((yaml.safe_load(f) or {}).get("tasks", []))

    if args.tasks and args.tasks != "all":
        wanted = [t.strip() for t in args.tasks.split(",") if t.strip()]
        by_id = {t["id"]: t for t in tasks}
        missing = [w for w in wanted if w not in by_id]
        if missing:
            raise SystemExit(f"Unknown task ids: {missing}")
        tasks = [by_id[w] for w in wanted]
    if args.category:
        tasks = [t for t in tasks if t.get("category") == args.category]
    if args.limit:
        tasks = tasks[:args.limit]

    results_path = args.out or RESULTS_PATH
    total_runs = len(tasks) * args.repeat

    print(f"\n========================================================")
    print(f"🛡️  SEGURO V2 EMPIRICAL EVALUATION SUITE")
    print(f"   Tasks: {len(tasks)} x {args.repeat} runs | Mode: {args.mode.upper()}")
    print(f"========================================================\n")

    results = []
    n = 0
    for t in tasks:
        for run_idx in range(1, args.repeat + 1):
            n += 1
            killswitch.reset()
            print(f"[{n}/{total_runs}] Running: {t['id']} (run {run_idx}) ...", end=" ", flush=True)
            res = await run_single_eval_task(t, mode=args.mode)
            res["run"] = run_idx
            res["mode"] = args.mode
            res["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
            status_str = {"PASS": "✅ PASS", "SKIP": "⏭️ SKIP"}.get(res["status"], "❌ FAIL")
            print(f"{status_str} ({res['elapsed_s']:.2f}s, steps: {res['steps']}, agent said: {res.get('agent_status')})")
            if res["status"] != "PASS":
                print(f"      -> {(res.get('error') or res.get('evidence') or '')[:200]}")
            results.append(res)

    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    report_md = generate_markdown_report(results, mode=args.mode)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"\n   Results saved to: {results_path}")
    print(f"   Report: {REPORT_PATH}")

if __name__ == "__main__":
    asyncio.run(main_eval())
